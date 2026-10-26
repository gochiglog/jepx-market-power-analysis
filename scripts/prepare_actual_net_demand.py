"""少数実績サンプルと既存観測からA/B仕様の判断用入力を作る。LPは実行しない。"""

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.actual_net_demand import (  # noqa: E402
    candidate_roles,
    clock_keys,
    hourly_energy_comparison,
    net_candidate,
    numeric_raw,
    regional_column_indices,
)
from src.fixed_output_feasibility import decode_csv  # noqa: E402
from src.net_demand_network import AREAS  # noqa: E402


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(root, output):
    output = output.resolve()
    if output.exists():
        raise ValueError("既存出力を上書きしない")
    output.mkdir(parents=True)
    qa_dir = root / "reports/19_actual_net_demand/qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    refs = root / "data/interim/research_reference/20261026_actual_supply_v1"
    registry_path = (
        root / "reports/18_fixed_output_feasibility/qa/limited_supply_asset_register.csv"
    )
    registry = pd.read_csv(registry_path, keep_default_na=False)
    roles = candidate_roles(registry)
    roles.to_csv(qa_dir / "fixed_adjustable_role_candidates.csv", index=False)
    fixed = roles[roles.candidate_analysis_role.eq("fixed_observed_nuclear_candidate")]
    adjustable = roles[roles.candidate_analysis_role.eq("adjustable_reference_candidate")]
    if len(fixed) != 32 or len(adjustable) != 385:
        raise ValueError("417限定集合の役割件数不一致")
    if set(fixed.asset_key_candidate) & set(adjustable.asset_key_candidate):
        raise ValueError("固定と調整が重複")
    manifest_path = root / "metadata/research/actual_supply_sources_20261026.json"
    manifest = json.loads(manifest_path.read_text())
    for record in manifest:
        if "path" in record and sha(root / record["path"]) != record["sha256"]:
            raise ValueError("追加原本SHA不一致")
    source_qa, tables, inputs = [], [], [registry_path, manifest_path]
    original_outputs = []
    datasets = {
        "北海道": refs / "hokkaido_202504.csv",
        "東北": refs / "tohoku_202504.csv",
        "東京": root
        / ("data/interim/research_reference/20261026_R_feasibility_v1/tokyo_actual_202504.csv"),
        "北陸": refs / "hokuriku_202504.csv",
        "関西": refs / "kansai_202504.csv",
        "中国": refs / "chugoku_202504.csv",
        "四国": refs / "shikoku_202504.csv",
        "九州": refs / "kyushu_202504.csv",
    }
    for area, path in datasets.items():
        rows, encoding = decode_csv(path.read_bytes())
        header = rows[1]
        if any(len(r) != len(header) for r in rows[2:]):
            raise ValueError("原CSV列数不一致")
        raw = pd.DataFrame(rows[2:], columns=header)
        original = pa.Table.from_pandas(raw, preserve_index=False)
        original_path = output / (path.stem + "_all_original_rows.parquet")
        pq.write_table(original, original_path)
        if not pq.read_table(original_path).equals(original):
            raise ValueError("元行保存不一致")
        original_outputs.append(
            {"path": str(original_path.relative_to(root)), "sha256": sha(original_path)}
        )
        padding = raw.eq("").all(axis=1)
        padding_count = int(padding.sum())
        raw = raw.loc[~padding].copy()
        time_role = "end" if area == "九州" else "start"
        keys = clock_keys(raw["DATE"], raw["TIME"], role=time_role)
        frame = pd.concat([raw, keys], axis=1)
        frame["area"] = area
        frame["source_file"] = str(path.relative_to(root))
        frame["source_sha256"] = sha(path)
        frame["source_row"] = raw.index + 3
        for target, source in {
            "demand_actual_sample_mw": "エリア需要",
            "pv_actual_sample_mw": "太陽光発電実績",
            "wind_actual_sample_mw": "風力発電実績",
            "nuclear_regional_sample_mw": "原子力",
            "pv_curtailment_sample_mw": "太陽光出力制御量",
            "wind_curtailment_sample_mw": "風力出力制御量",
        }.items():
            frame[target] = frame[source].map(numeric_raw)
        frame["renewable_actual_sample_mw"] = [
            None if p is None or w is None else p + w
            for p, w in zip(frame.pv_actual_sample_mw, frame.wind_actual_sample_mw, strict=True)
        ]
        frame["clock_role_contract"] = time_role + "_source_48_interval_sequence"
        frame["source_file_update_fields"] = None
        frame["source_zip_member"] = None
        if not frame[header].equals(raw):
            raise ValueError("元セル不一致")
        qa = {
            "area": area,
            "rows": len(raw),
            "encoding": encoding,
            "unit_header": rows[0],
            "raw_missing_cells": int(raw.eq("").sum().sum()),
            "all_empty_padding_rows_preserved_separately": padding_count,
            "clock_role": time_role,
            "full_original_columns_preserved": True,
        }
        source_qa.append(qa)
        tables.append(frame)
        inputs.append(path)
    # 中部は地域全PV/風力を持つ30分実績。月ZIPの予測/BG計画は入力に混ぜない。
    path = refs / "chubu_202504_keito.zip"
    inputs.append(path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        actual_names = [n for n in names if n.endswith("_keito_jisseki.csv")]
        for name in actual_names:
            rows, encoding = decode_csv(archive.read(name))
            raw = pd.DataFrame(rows[3:], columns=rows[2])
            keys = clock_keys(raw["日付"], raw["時間帯_自"], role="start")
            if not (keys["period"].to_numpy() == raw["時間コマ"].astype(int).to_numpy()).all():
                raise ValueError("中部コマと時刻不一致")
            if raw["時間帯_至"].iloc[-1] != "24:00":
                raise ValueError("中部日末境界不一致")
            frame = pd.concat([raw, keys], axis=1)
            frame["area"] = "中部"
            frame["source_file"] = str(path.relative_to(root))
            frame["source_sha256"] = sha(path)
            frame["source_row"] = range(4, len(frame) + 4)
            frame["source_zip_member"] = name
            frame["source_file_update_fields"] = json.dumps(rows[1], ensure_ascii=False)
            for target, source in {
                "demand_actual_sample_mw": "エリア総需要量",
                "renewable_actual_sample_mw": "エリア風力・太陽光発電量",
            }.items():
                frame[target] = raw[source].map(
                    lambda v: None if numeric_raw(v) is None else numeric_raw(v) / 500
                )
            for column in [
                "pv_actual_sample_mw",
                "wind_actual_sample_mw",
                "nuclear_regional_sample_mw",
                "pv_curtailment_sample_mw",
                "wind_curtailment_sample_mw",
            ]:
                frame[column] = None
            frame["clock_role_contract"] = "explicit_start_end_period"
            if not frame[raw.columns].equals(raw):
                raise ValueError("中部元セル不一致")
            tables.append(frame)
    source_qa.append(
        {
            "area": "中部",
            "rows": sum(len(t) for t in tables if t.area.iloc[0] == "中部"),
            "unit": "kWh/30min -> MW: /0.5/1000",
            "zip_members": len(names),
            "actual_members": len(actual_names),
            "forecast_BG_members_not_used": len(names) - len(actual_names),
            "nuclear_regional_missing_is_not_zero": True,
        }
    )
    sample = pd.concat(tables, ignore_index=True)
    keys = ["area", "delivery_date", "period"]
    if len(sample) != 12960 or sample.duplicated(keys).any():
        raise ValueError("9地域4月キー不一致")
    if (
        not sample.groupby(["area", "delivery_date"])
        .period.apply(lambda x: sorted(x) == list(range(1, 49)))
        .all()
    ):
        raise ValueError("48コマ不完全")
    # REIは検算専用。1点も一次値を置換しない。
    comparisons, rei_qa, filename_review = [], [], []
    for ascii_name, expected_area in zip(
        [
            "hokkaido",
            "tohoku",
            "tokyo",
            "chubu",
            "hokuriku",
            "kansai",
            "chugoku",
            "shikoku",
            "kyushu",
        ],
        AREAS,
        strict=True,
    ):
        old = root / f"data/raw/rei/supply_demand/rei_supply_demand_{ascii_name}_FY2025.csv"
        old_rows, _ = decode_csv(old.read_bytes())
        actual_area = old_rows[0][2]
        filename_review.append(
            {
                "path": str(old.relative_to(root)),
                "expected_area_by_filename": expected_area,
                "area_in_original_header": actual_area,
                "matches_filename": actual_area == expected_area,
                "sha256": sha(old),
                "action": "原本保持。今回の検算には統合CSVのヘッダーを使用",
            }
        )
        inputs.append(old)
    pd.DataFrame(filename_review).to_csv(qa_dir / "rei_filename_header_review.csv", index=False)
    rei_path = root / "data/raw/rei/power_data/rei_power_data_FY2025.csv"
    inputs.append(rei_path)
    rows, _ = decode_csv(rei_path.read_bytes())
    raw = pd.DataFrame(rows[2:], columns=[f"raw_{i}" for i in range(len(rows[1]))])
    for area in AREAS:
        indices = regional_column_indices(
            rows[0], rows[1], area, ["需要", "太陽光", "風力", "原子力"]
        )
        rei_keys = clock_keys(raw.raw_0, raw.raw_1, role="start")
        rei = rei_keys[["delivery_date", "period"]].copy()
        for label, i in indices.items():
            rei["rei__" + label] = raw[f"raw_{i}"].map(numeric_raw)
        sub = sample[sample.area.eq(area)]
        joined = sub.merge(
            rei,
            on=["delivery_date", "period"],
            how="left",
            validate="one_to_one",
            indicator="rei_join",
        )
        if not joined.rei_join.eq("both").all():
            raise ValueError("REIキー未一致")
        for column, label in [
            ("demand_actual_sample_mw", "需要"),
            ("nuclear_regional_sample_mw", "原子力"),
            ("pv_actual_sample_mw", "太陽光"),
            ("wind_actual_sample_mw", "風力"),
        ]:
            pairs = joined[[column, "rei__" + label]].dropna()
            rei_qa.append(
                {
                    "area": area,
                    "variable": column,
                    "comparable_cells": len(pairs),
                    "match_cells": int(pairs[column].eq(pairs["rei__" + label]).sum()),
                    "not_comparable_cells": len(joined) - len(pairs),
                }
            )
        rei_renewable = joined["rei__太陽光"] + joined["rei__風力"]
        rei_qa.append(
            {
                "area": area,
                "variable": "renewable_actual_sample_mw",
                "comparable_cells": len(joined),
                "match_cells": int(joined.renewable_actual_sample_mw.eq(rei_renewable).sum()),
                "not_comparable_cells": 0,
            }
        )
        comparisons.extend(
            joined[
                keys
                + [
                    "demand_actual_sample_mw",
                    "rei__需要",
                    "renewable_actual_sample_mw",
                    "rei__太陽光",
                    "rei__風力",
                ]
            ].to_dict("records")
        )
    pd.DataFrame(rei_qa).to_csv(qa_dir / "primary_vs_rei.csv", index=False)
    # 32原子力ラベルの観測出力も別候補に保持。地域全原子力とは区別する。
    nuclear_path = root / (
        "data/interim/occto/public_record_annotation/20261016_diagnostic_A_v1/"
        "generation_with_snapshot_public_records_FY2025.parquet"
    )
    inputs.append(nuclear_path)
    nuclear = pq.read_table(
        nuclear_path,
        columns=["asset_key_candidate", "area", "delivery_date", "period", "generation_kwh_30min"],
        filters=[
            ("asset_key_candidate", "in", fixed.asset_key_candidate.tolist()),
            ("delivery_date", ">=", date(2025, 4, 1)),
            ("delivery_date", "<", date(2025, 5, 1)),
        ],
    ).to_pandas()
    if nuclear.duplicated(["asset_key_candidate", "delivery_date", "period"]).any():
        raise ValueError("原子力観測重複")
    expected = fixed.groupby("area").size().to_dict()
    grouped = (
        nuclear.groupby(keys)
        .agg(
            nuclear_label_observed_count=("asset_key_candidate", "size"),
            nuclear_generation_known_count=("generation_kwh_30min", "count"),
            nuclear_generation_kwh_partial=("generation_kwh_30min", lambda x: x.sum(min_count=1)),
        )
        .reset_index()
    )
    sample = sample.merge(
        grouped, on=keys, how="left", validate="one_to_one", indicator="nuclear_join"
    )
    sample["nuclear_label_scope_count"] = sample.area.map(expected).fillna(0).astype(int)
    limited_values = []
    for r in sample.to_dict("records"):
        count = r["nuclear_label_scope_count"]
        if count == 0:
            value = numeric_raw("0")  # 明示した32ラベル集合の空集合和。地域全原子力0ではない。
        elif (
            r["nuclear_label_observed_count"] == count
            and r["nuclear_generation_known_count"] == count
        ):
            value = r["nuclear_generation_kwh_partial"] / 500
        else:
            value = None
        limited_values.append(value)
    sample["nuclear_observed_subset_candidate_mw"] = limited_values
    plan_path = root / (
        "data/processed/occto/demand_supply_direction/20261016_native_v1/"
        "occto_reserve_nextday2_FY2025.parquet"
    )
    inputs.append(plan_path)
    plan = pd.read_parquet(
        plan_path,
        columns=keys
        + [
            "area_demand_forecast_mw",
            "source_record_id",
            "file_latest_updated_at",
            "source_downloaded_at",
        ],
    )
    sample = sample.merge(
        plan.rename(
            columns={
                "source_record_id": "plan_source_record_id",
                "source_downloaded_at": "plan_downloaded_at",
            }
        ),
        on=keys,
        how="left",
        validate="one_to_one",
        indicator="plan_join",
    )
    if not sample.plan_join.eq("both").all():
        raise ValueError("翌々日需要キー不一致")
    for letter, demand_col in [("A", "area_demand_forecast_mw"), ("B", "demand_actual_sample_mw")]:
        for scope, nuclear_col in [
            ("regional", "nuclear_regional_sample_mw"),
            ("observed_subset", "nuclear_observed_subset_candidate_mw"),
        ]:
            sample[f"candidate_net_{letter}_{scope}_mw"] = [
                net_candidate(d, r, n)
                for d, r, n in zip(
                    sample[demand_col],
                    sample.renewable_actual_sample_mw,
                    sample[nuclear_col],
                    strict=True,
                )
            ]
    sample["research_scope_adopted"] = False
    sample["nuclear_metering_alignment_verified"] = False
    outputs = list(original_outputs)
    for name, frame in [
        ("actual_sample_and_plan_candidates.parquet", sample),
        ("primary_rei_comparison.parquet", pd.DataFrame(comparisons)),
        ("nuclear_observed_subset_raw.parquet", nuclear),
        ("hourly_energy_comparison.parquet", hourly_energy_comparison(sample)),
    ]:
        destination = output / name
        for column in frame.select_dtypes(include="category").columns:
            frame[column] = frame[column].astype("string")
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, destination, compression="zstd")
        restored = pq.read_table(destination)
        if not restored.equals(table):
            bad = [c for c in table.column_names if not table[c].equals(restored[c])]
            raise ValueError(f"Parquet再読込不一致: {bad}")
        outputs.append({"path": str(destination.relative_to(root)), "sha256": sha(destination)})
    audit = {
        "source_samples": source_qa,
        "rows": len(sample),
        "days": 30,
        "areas": sorted(sample.area.unique()),
        "duplicate_keys": 0,
        "plan_join": sample.plan_join.value_counts().to_dict(),
        "rei_key_matched": len(sample),
        "nuclear_subset_join": sample.nuclear_join.value_counts().to_dict(),
        "role_counts": roles.candidate_analysis_role.value_counts().to_dict(),
        "nine_area_role_counts": roles[roles.area.isin(AREAS)]
        .candidate_analysis_role.value_counts()
        .to_dict(),
        "outside_nine_area_role_counts": roles[~roles.area.isin(AREAS)]
        .candidate_analysis_role.value_counts()
        .to_dict(),
        "fixed_adjustable_overlap": 0,
        "nuclear_expected_by_area": expected,
        "candidate_net_nulls": {
            c: int(sample[c].isna().sum()) for c in sample if c.startswith("candidate_net_")
        },
        "candidate_net_nonpositive": {
            c: int(sample[c].dropna().le(0).sum()) for c in sample if c.startswith("candidate_net_")
        },
        "real_lp_runs": 0,
        "firm_rsi_calculated": False,
        "inputs": [{"path": str(p.relative_to(root)), "sha256": sha(p)} for p in inputs],
        "outputs": outputs,
    }
    (qa_dir / "preparation_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        {
            k: audit[k]
            for k in ["rows", "role_counts", "candidate_net_nulls", "candidate_net_nonpositive"]
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.output)
