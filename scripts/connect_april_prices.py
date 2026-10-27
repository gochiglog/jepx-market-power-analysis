"""工程24：既存入力2点の追跡と4月の記述用価格接続。旧版は上書きしない。"""

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.trial_adjustable_removal import ENTITIES, TARGETS  # noqa: E402
from src.april_price_connection import (  # noqa: E402
    STATES,
    distribution,
    require_join,
    signed_transfer_envelope,
)
from src.fixed_output_feasibility import decode_csv  # noqa: E402
from src.net_demand_network import AREAS  # noqa: E402

BASE = Path("data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3")
COMP = Path("data/interim/occto/april_trial_comparison/20261027_comparison_v1")
CONS = Path("data/interim/occto/april_constraint_diagnosis/20261027_constraints_v2")
KEY = ["delivery_date", "period"]
APRIL = [("delivery_date", ">=", date(2025, 4, 1)), ("delivery_date", "<=", date(2025, 4, 30))]


def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(root, output, report):
    if output.exists() or (report / "qa/run_audit.json").exists():
        raise ValueError("既存版へ上書きしない")
    output.mkdir(parents=True)
    qa = report / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    inputs = {}
    outputs = []

    def read(p, **kw):
        inputs[str(p.relative_to(root))] = sha(p)
        return pq.read_table(p, **kw)

    def write(frame, name, overrides=None):
        p = output / (name + ".parquet")
        f = frame.copy()
        for col in f.select_dtypes(include="category"):
            f[col] = f[col].astype(str)
        t = pa.Table.from_pandas(f, preserve_index=False)
        for col, dtype in (overrides or {}).items():
            if col in t.column_names:
                t = t.set_column(
                    t.column_names.index(col), pa.field(col, dtype), t[col].cast(dtype)
                )
        pq.write_table(t, p, compression="zstd")
        assert pq.read_table(p).equals(t)
        outputs.append(dict(path=str(p.relative_to(root)), sha256=sha(p), rows=len(f)))

    # 北海道の3ラベルの集合差。再調査・燃種補完・復帰はしない。
    oldpath = root / "reports/13_hokkaido_scatter/qa/asset_reference_registry.csv"
    inputs[str(oldpath.relative_to(root))] = sha(oldpath)
    old = pd.read_csv(oldpath, keep_default_na=False)
    old = old[old.reference_classification.eq("non_target")]
    assets = read(root / BASE / "asset_attribution_proxy.parquet").to_pandas()
    chosen = require_join(
        old, assets, ["asset_key_candidate"], validate="one_to_one", suffixes=("_scatter", "_trial")
    )
    assert len(chosen) == 3
    assert not chosen.removal_entity_code.eq("hokkaido").any()
    units = read(root / BASE / "unit_trial_uppers/reference_no_deduction.parquet").to_pandas()
    units = units[units.asset_key_candidate.isin(chosen.asset_key_candidate)]
    detail = []
    for x in chosen.to_dict("records"):
        g = units[units.asset_key_candidate.eq(x["asset_key_candidate"])]
        ref = g[(g.delivery_date == date(2025, 4, 1)) & g.period.eq(1)]
        assert len(ref) == 1
        u = ref.iloc[0]
        detail.append(
            dict(
                asset_key_candidate=x["asset_key_candidate"],
                plant_name=x["plant_name"],
                unit_name=x["unit_name"],
                raw_fuel=x["発電方式・燃種"],
                business_name=x["hjks_business_name_unique_reference_trial"],
                scatter_capacity_mw=Decimal(
                    str(x["diagnostic_mapping__capacity_reference_raw_kw_scatter"])
                )
                / Decimal(1000),
                trial_reference_capacity_mw=u.capacity_reference_kw / Decimal(1000)
                if u.capacity_reference_kw is not None
                else None,
                scatter_included=True,
                trial_adjustable_included=bool(x["eligible_adjustable_candidate"]),
                limited_scope_status=x["limited_scope_status"],
                mapping_status=x["diagnostic_mapping__research_use_status"],
                trial_role=x["candidate_analysis_role"],
                removal_entity_code=x["removal_entity_code"],
                trial_status_at_example=u.scenario_status,
                trial_upper_at_example_mw=u.trial_adjustable_upper_kw / Decimal(1000)
                if u.trial_adjustable_upper_kw is not None
                else None,
                example_delivery_date=u.delivery_date,
                example_period=int(u.period),
                april_observed_cells=len(g),
                april_trial_defined_cells=int(g.trial_upper_defined.sum()),
                exclusion_due_to_fuel_scope=not bool(x["pv_wind_disjoint_by_raw_fuel"]),
                exclusion_due_to_target_removal=False,
                automatic_reinstatement=False,
                hjks_rows_json=x["hjks_candidate_rows_json_trial"],
            )
        )
    registry = pd.DataFrame(detail)
    write(registry, "hokkaido_supply_set_reconciliation")
    registry.to_csv(qa / "hokkaido_supply_set_reconciliation.csv", index=False)
    assert (
        sum(registry.scatter_capacity_mw) == 462
        and sum(registry.trial_upper_at_example_mw.dropna()) == 250
    )
    # 代表例22元行をそのまま保存し、公式原CSVの全19列と再照合。
    network_table = read(
        root
        / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1"
        / "network_candidate_bounds.parquet"
    )
    network = network_table.to_pandas()
    example = network[(network.delivery_date == date(2025, 4, 1)) & network.period.eq(1)].copy()
    assert len(example) == 22
    for path, g in example.groupby("source_file"):
        raw = root / path
        inputs[path] = sha(raw)
        rows, encoding = decode_csv(raw.read_bytes())
        header = rows[0]
        for row in g.to_dict("records"):
            cells = rows[int(row["source_row"]) - 1]
            assert len(cells) == len(header)
            for col, cell in zip(header, cells, strict=True):
                assert row[col] == cell, (col, row[col], cell)
    bounds = []
    for row in example.to_dict("records"):
        d = signed_transfer_envelope(
            row["operating_capacity_mw"],
            row["margin_mw"],
            row["wide_area_adjustment_mw"],
            row["planned_flow_mw"],
            row["available_capacity_mw"],
        )
        assert d["available_identity_matches"]
        bounds.append(d["model_bound"])
    example["reviewed_signed_model_bound_mw"] = bounds
    example["bound_formula"] = "C_signed - M_signed - W_signed"
    example["available_formula"] = "A_signed = C_signed - M_signed - W_signed - P_signed"
    example["review_status"] = "conditional_scenario_no_numeric_correction"
    example["fixed_reservations_assumption"] = (
        "保存マージン・広域調整枠を保持し、残余を純移送の再配分包絡とする。登録済み利用の権利・実運用・市場時点再現ではない"
    )
    write(
        example,
        "representative_network_original_rows",
        {f.name: f.type for f in network_table.schema},
    )
    cols = [
        "link_name_raw",
        "direction_raw",
        "sending_area_set_json",
        "receiving_area_set_json",
        "delivery_date",
        "period",
        "年月日",
        "時刻",
        "最新更新年月日時刻",
        "timestamp_start",
        "timestamp_end",
        "対象断面",
        "策定日",
        "策定／更新後",
        "運用容量",
        "マージン",
        "広域調整枠",
        "計画潮流",
        "空容量",
        "reviewed_signed_model_bound_mw",
        "bound_formula",
        "available_formula",
        "source_file",
        "source_row",
        "source_record_id",
        "row_updated_at",
        "source_acquired_at",
        "review_status",
        "fixed_reservations_assumption",
    ]
    trace = example[cols].copy()
    trace["official_definition_url"] = (
        "https://www.occto.or.jp/houkokusho/2025/files/denryokujukyuu_2024_250910.pdf"
    )
    trace["official_definition_page"] = "物理36／本文33 図2-17・表2-7"
    trace["csv_definition_url"] = (
        "https://www.occto.or.jp/assets/occtosystem2/files/250306_keitoujouhou.pdf"
    )
    trace["csv_definition_page"] = "物理43〜44 表2-8"
    trace["total_reallocation_basis"] = "Cが全体の上端、AがPを控除した余地という定義からA+Pへ戻す"
    trace["unverified_scope"] = "M/W固定、再配分権利・実物理上限・全FYの設定内訳は未確認"
    trace.to_csv(qa / "representative_network_trace.csv", index=False)
    # 全4月で原数値の式を照合。計画潮流0を意味の同一性の根拠にはしない。
    net_april = network[
        (network.delivery_date >= date(2025, 4, 1)) & (network.delivery_date <= date(2025, 4, 30))
    ]
    arithmetic = (
        net_april.operating_capacity_mw
        - net_april.margin_mw
        - net_april.wide_area_adjustment_mw
        - net_april.planned_flow_mw
    ).eq(net_april.available_capacity_mw)
    pd.DataFrame(
        [
            dict(
                rows=len(net_april),
                available_identity_matches=int(arithmetic.sum()),
                planned_nonzero=int(net_april.planned_flow_mw.ne(0).sum()),
                meaning_verified_by_zero_flow=False,
                model_bounds_changed=False,
            )
        ]
    ).to_csv(qa / "network_arithmetic_check.csv", index=False)
    # 一次spot価格。元43列の値・型を保持して9評価地域へ展開。
    spot_all = read(root / "data/processed/jepx/spot/jepx_spot_FY2025.parquet")
    spot = spot_all.to_pandas()
    sens = read(
        root / "data/processed/jepx/price_sensitivity/jepx_price_sensitivity_FY2025.parquet"
    ).to_pandas()
    ss = require_join(
        spot, sens[KEY + ["system_price_sensitivity_base"]], KEY, validate="one_to_one"
    )
    ss["baseline_price_match_flag"] = ss.system_price.eq(ss.system_price_sensitivity_base)
    assert (~ss.baseline_price_match_flag).sum() == 2271
    ss = ss[(ss.delivery_date >= date(2025, 4, 1)) & (ss.delivery_date <= date(2025, 4, 30))].copy()
    assert len(ss) == 1440
    area_columns = dict(
        zip(
            AREAS,
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
            strict=True,
        )
    )
    pieces = []
    for area, code in TARGETS:
        g = ss.copy()
        g["target_area"] = area
        g["entity_code"] = code
        g["entity"] = ENTITIES[code]
        g["area_price"] = g["area_price_" + area_columns[area]]
        g["system_price_market"] = g.system_price
        pieces.append(g)
    price = pd.concat(pieces, ignore_index=True)
    assert len(price) == 12960 and not price.duplicated(["target_area"] + KEY).any()
    overrides = {field.name: field.type for field in spot_all.schema}
    price_type = spot_all.schema.field("system_price").type
    overrides.update(
        {
            k: price_type
            for k in ["area_price", "system_price_market", "system_price_sensitivity_base"]
        }
    )
    write(price, "april_price_observations", overrides)
    primary = read(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    ).to_pandas()
    primary = primary.rename(
        columns={
            "area": "target_area",
            "demand_actual_sample_mw": "observed_demand_mw",
            "fixed_actual_mw": "observed_fixed_R_mw",
            "net_demand_B_mw": "observed_net_demand_mw",
        }
    )[
        ["target_area"]
        + KEY
        + ["observed_demand_mw", "observed_fixed_R_mw", "observed_net_demand_mw"]
    ]
    base = require_join(price, primary, ["target_area"] + KEY, validate="one_to_one")
    base["hour_start"] = (base.period - 1) // 2
    rtable = read(root / BASE / "all_and_removed_net_multipliers.parquet")
    r = rtable.to_pandas()
    keys = ["target_area", "entity_code"] + KEY
    right = base.rename(columns={col: "price__" + col for col in base if col not in keys})
    joined = require_join(r, right, keys, validate="many_to_one")
    assert len(joined) == 440640
    for col in [
        "area_price",
        "system_price_market",
        "system_price_sensitivity_base",
        "baseline_price_match_flag",
        "observed_demand_mw",
        "observed_fixed_R_mw",
        "observed_net_demand_mw",
        "hour_start",
    ]:
        joined[col] = joined["price__" + col]
    assert np.allclose(
        joined.target_net_demand_mw.to_numpy(float), joined.observed_net_demand_mw.to_numpy(float)
    )
    sim = read(root / COMP / "nine_area_simultaneous_after_removal.parquet").to_pandas()
    sk = ["supply_trial_scenario", "network_scenario", "entity_code"] + KEY
    sim = sim[sk + ["all_firms_status", "removed_simultaneous_status"]].rename(
        columns={
            "all_firms_status": "global_all_firms_status",
            "removed_simultaneous_status": "global_removed_simultaneous_status",
        }
    )
    joined = require_join(joined, sim, sk, validate="many_to_one")
    jt = {f.name: f.type for f in rtable.schema}
    jt.update({"price__" + k: v for k, v in overrides.items() if k not in keys})
    jt.update(
        {
            k: price_type
            for k in ["area_price", "system_price_market", "system_price_sensitivity_base"]
        }
    )
    write(joined, "all_scenarios_with_spot_prices", jt)
    # 17条件の一致区分は8法人×2送電。JERAだけ地域価格2個へ対応する。
    cons = read(root / CONS / "condition_consistency_all_times.parquet").to_pandas()
    b = pd.concat(
        [
            base.assign(network_scenario=ns)
            for ns in ["saved_plan_constraints", "no_transmission_bounds"]
        ],
        ignore_index=True,
    )
    consprice = require_join(
        b, cons, ["entity_code", "network_scenario"] + KEY, validate="many_to_one"
    )
    assert len(consprice) == 25920
    write(consprice, "consistency_with_prices_by_network", overrides)
    summary = []
    hours = []
    subset = consprice[consprice.network_scenario.eq("saved_plan_constraints")]
    subset.assign(net_nonpositive=subset.observed_net_demand_mw.le(0)).groupby(
        ["target_area", "consistency_state"]
    ).agg(
        count=("period", "size"), net_nonpositive=("net_nonpositive", "sum")
    ).reset_index().to_csv(qa / "state_nonpositive_net_counts.csv", index=False)
    for area in AREAS:
        g = subset[subset.target_area.eq(area)]
        assert len(g) == 1440
        for state in STATES:
            z = g[g.consistency_state.eq(state)]
            for variable in [
                "area_price",
                "observed_demand_mw",
                "observed_net_demand_mw",
                "hour_start",
            ]:
                summary.append(
                    dict(
                        target_area=area,
                        consistency_state=state,
                        variable=variable,
                        **distribution(z[variable]),
                    )
                )
            for h in range(24):
                hours.append(
                    dict(
                        target_area=area,
                        consistency_state=state,
                        hour_start=h,
                        count=int(z.hour_start.eq(h).sum()),
                        denominator=len(z),
                        within_state_share=float(z.hour_start.eq(h).mean()) if len(z) else None,
                    )
                )
    pd.DataFrame(summary).to_csv(qa / "state_price_and_covariate_distribution.csv", index=False)
    pd.DataFrame(hours).to_csv(qa / "state_hour_distribution.csv", index=False)
    ref = joined[
        joined.supply_trial_scenario.eq("reference_no_deduction")
        & joined.network_scenario.eq("saved_plan_constraints")
    ]
    ref.groupby(["target_area", "status"]).size().rename("count").reset_index().to_csv(
        qa / "scatter_all_status_counts.csv", index=False
    )
    stats = []
    windows = []
    examples = []
    for area in AREAS:
        g = ref[ref.target_area.eq(area)]
        valid = g[g.status.eq("valid")]
        windows.append(
            dict(
                target_area=area,
                all_times=len(g),
                valid=len(valid),
                unplottable=len(g) - len(valid),
                above_zoom_2=int(valid.lambda_removed.gt(2).sum()),
                max_multiplier=valid.lambda_removed.max(),
            )
        )
        for status, z in g.groupby("status"):
            stats.append(dict(target_area=area, status=status, **distribution(z.area_price)))
        for rule, mask, ascending in [
            ("有効・倍率1未満の最小価格（同値は最初）", valid.lambda_removed.lt(1 - 1e-8), True),
            ("有効・倍率1以上の最大価格（同値は最初）", valid.lambda_removed.gt(1 + 1e-8), False),
        ]:
            z = valid[mask].sort_values(
                ["area_price", "delivery_date", "period"], ascending=[ascending, True, True]
            )
            if z.empty:
                examples.append(dict(target_area=area, rule=rule, example_status="該当なし"))
            else:
                x = z.iloc[0]
                examples.append(
                    dict(
                        target_area=area,
                        rule=rule,
                        example_status="observed",
                        delivery_date=x.delivery_date,
                        period=int(x.period),
                        area_price=x.area_price,
                        lambda_removed=x.lambda_removed,
                        observed_demand_mw=x.observed_demand_mw,
                        observed_net_demand_mw=x.observed_net_demand_mw,
                    )
                )
    pd.DataFrame(stats).to_csv(qa / "scatter_status_price_distribution.csv", index=False)
    pd.DataFrame(windows).to_csv(qa / "scatter_display_windows.csv", index=False)
    pd.DataFrame(examples).to_csv(qa / "descriptive_extreme_examples.csv", index=False)
    for path, h in inputs.items():
        assert sha(root / path) == h
    audit = dict(
        version=output.name,
        inputs=[dict(path=p, sha256=h) for p, h in inputs.items()],
        outputs=outputs,
        unique_market_observations=12960,
        alternative_scenario_rows=440640,
        scenario_multiplier=34,
        consistency_rows=25920,
        join_match_rate=1.0,
        unmatched=0,
        duplicates=0,
        silent_drop=0,
        fy2025_system_baseline_mismatches=2271,
        april_system_baseline_mismatches=int((~ss.baseline_price_match_flag).sum()),
        raw_representative_rows_all_cells_equal=22,
        hokkaido_automatically_reinstated=False,
        network_bounds_changed=False,
        final_baseline_adopted=False,
        regression_or_significance_test=False,
    )
    (qa / "run_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    (output / "manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print("価格接続完了", 12960, 440640)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    run(Path(__file__).resolve().parents[1], a.output.resolve(), a.report.resolve())
