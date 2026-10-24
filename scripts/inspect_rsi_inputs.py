"""RSI設計のための読み取り検査。RSI算定・設備帰属付与は行わない。"""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

AREAS = ["北海道", "東北", "東京", "中部", "北陸", "関西", "中国", "四国", "九州"]
KEY = ["area", "delivery_date", "period"]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inspect(root):
    artifacts = json.loads((root / "metadata/artifact_catalog.json").read_text())["artifacts"]
    inputs = []

    def load(token, columns=None):
        paths = [r["path"] for r in artifacts if token in r["path"]]
        if len(paths) != 1:
            raise ValueError("入力を一意に特定できない: " + token)
        path = root / paths[0]
        inputs.append({"path": paths[0], "sha256": digest(path)})
        return pq.read_table(path, columns=columns).to_pandas()

    targets_path = root / "metadata/research/regulatory_targets.csv"
    targets = pd.read_csv(targets_path, dtype=str)
    if targets.duplicated(["regulatory_entity", "market_area"]).any():
        raise ValueError("対象法人×エリアの重複")
    if len(targets) != 11 or targets.regulatory_entity.nunique() != 10:
        raise ValueError("10法人・11法人×エリアの対象範囲と不一致")
    if not ((targets.valid_from <= "2025-04-01") & (targets.valid_to >= "2026-03-31")).all():
        raise ValueError("FY2025全期間を対象期間が覆っていない")
    inputs.append({"path": str(targets_path.relative_to(root)), "sha256": digest(targets_path)})
    aliases_path = root / "metadata/research/legal_name_alias_evidence_20261016.csv"
    aliases = pd.read_csv(aliases_path, dtype=str)
    verified = aliases[aliases.verification_level.eq("official_html_verified")]
    if verified.alias_raw.duplicated().any():
        raise ValueError("法人略称の複数対応")
    lookup = dict(zip(verified.alias_raw, verified.canonical_legal_name, strict=True))
    inputs.append({"path": str(aliases_path.relative_to(root)), "sha256": digest(aliases_path)})
    units = load("observed_unit_business_evidence_FY2025")
    if len(units) != 491 or units.asset_key_candidate.duplicated().any():
        raise ValueError("元観測491ラベルの一意性不一致")
    units = units.assign(
        name_reference=units.hjks_business_name_unique_reference.map(lambda x: lookup.get(x, x))
    )
    nine = units[units.area.isin(AREAS)]
    checked = nine[nine.hjks_business_name_reference_status.eq("scope_checked_unique_reference")]
    pairs = []
    for target in targets.itertuples():
        local = checked[
            checked.area.eq(target.market_area)
            & checked.name_reference.eq(target.regulatory_entity)
        ]
        system = checked[checked.name_reference.eq(target.regulatory_entity)]
        pairs.append(
            {
                "regulatory_entity": target.regulatory_entity,
                "market_area": target.market_area,
                "valid_from": target.valid_from,
                "valid_to": target.valid_to,
                "verification_level": target.verification_level,
                "source_url": target.source_url,
                "scope_checked_name_reference_units_in_area": len(local),
                "scope_checked_name_reference_units_all_nine_areas": len(system),
                "observed_generation_kwh_name_reference_in_area": (
                    str(local.generation_sum_kwh.sum()) if len(local) else None
                ),
                "controlled_available_supply_mw": None,
                "availability_status": "不足：FY2025の支配・契約供給力は未確定",
                "name_reference_status": "一意な台帳記載者への名称参照"
                if len(local)
                else "参照なし（供給ゼロではない）",
                "asset_attribution_assigned": False,
                "rsi_computed": False,
            }
        )
    plan = load(
        "occto_reserve_nextday2_FY2025",
        KEY + ["area_demand_forecast_mw", "area_supply_plan_mw", "file_latest_updated_at"],
    )
    plan9 = plan[plan.area.isin(AREAS)]
    if len(plan9) != 157680 or plan9.duplicated(KEY).any():
        raise ValueError("予測需要の9エリア×17520キー不一致")
    sizes = plan9.groupby(["area", "delivery_date"]).size()
    if not sizes.eq(48).all() or set(plan9.period) != set(range(1, 49)):
        raise ValueError("予測需要の48コマ不一致")
    for area, frame in plan9.groupby("area"):
        if frame.delivery_date.nunique() != 365:
            raise ValueError(area + "の日数不一致")
    hk = load("hokkaido_asset_registry_FY2025")
    hk_others = hk[hk.reference_classification.eq("non_target")]
    # 既存の明示分類を参照するだけ。新しい法人帰属・RSIは作らない。
    hk_ref_kw = sum(int(v) for v in hk_others.diagnostic_mapping__capacity_reference_raw_kw)
    link = load(
        "system_capacity_dayahead2_planned_direction_comparison_FY2025",
        ["source_record_id", "link_name_raw", "direction_status", "sending_area", "receiving_area"],
    )
    if link.source_record_id.duplicated().any():
        raise ValueError("連系線元行IDの重複")
    qa = {
        "purpose": "RSIの入力可能範囲だけを診断。RSI値・割合・順位曲線は算定しない。",
        "target_areas": AREAS,
        "legal_entities": 10,
        "area_entity_pairs": 11,
        "planned_target_time_cells": 11 * 17520,
        "observed_units_all_ten_areas": len(units),
        "observed_units_nine_areas": len(nine),
        "scope_checked_name_reference_units_nine_areas": len(checked),
        "plan_demand_nine_area_rows": len(plan9),
        "plan_demand_duplicate_keys": 0,
        "plan_demand_nulls": int(plan9.area_demand_forecast_mw.isna().sum()),
        "plan_supply_nulls": int(plan9.area_supply_plan_mw.isna().sum()),
        "plan_demand_nonpositive_rows": sum(v <= 0 for v in plan9.area_demand_forecast_mw),
        "plan_demand_time_range": [str(plan9.delivery_date.min()), str(plan9.delivery_date.max())],
        "system_capacity_rows": len(link),
        "single_sending_receiving_area_rows": int(
            (link.sending_area.notna() & link.receiving_area.notna()).sum()
        ),
        "hk_reference_non_target_units": len(hk_others),
        "hk_reference_non_target_capacity_kw": hk_ref_kw,
        "hk_reference_non_target_capacity_mw": str(hk_ref_kw / 1000),
        "unit_conversion": "参照認可出力kW / 1000 = MW。新しいRSIや利用可能供給力ではない。",
        "minimal_two_area_scope_checked_labels": {
            area: int(checked.area.eq(area).sum()) for area in ["北海道", "東北"]
        },
        "minimal_two_area_reference_capacity_kw": {
            area: sum(
                int(v)
                for v in checked.loc[
                    checked.area.eq(area), "diagnostic_mapping__capacity_reference_raw_kw"
                ]
            )
            for area in ["北海道", "東北"]
        },
        "controlled_supply_missing_pairs": len(pairs),
        "rsi_computed": False,
        "fixed_versions_changed": False,
        "inputs": inputs,
    }
    for item in inputs:
        if digest(root / item["path"]) != item["sha256"]:
            raise ValueError("入力が検査中に変更された")
    return pd.DataFrame(pairs), qa


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pairs, qa = inspect(root)
    args.output.mkdir(parents=True, exist_ok=True)
    pairs.to_csv(args.output / "target_pair_readiness.csv", index=False, lineterminator="\n")
    (args.output / "input_readiness.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        "11対象の入力診断を保存。RSI未算定。北海道参照群: "
        f"{qa['hk_reference_non_target_capacity_mw']} MW"
    )


if __name__ == "__main__":
    main()
