"""純需要会計の入力点検と、既存公式根拠を持つ公表制約の数値化。"""

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.net_demand_network import (  # noqa: E402
    AREAS,
    FixedOutputContract,
    compile_legacy_constraints,
    solve_fixed_output,
)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def csv_write(path, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def run(root, output):
    if output.exists():
        raise ValueError("既存出力先を上書きしない")
    report = root / "reports/17_net_demand_network/qa"
    report.mkdir(parents=True, exist_ok=True)
    old = root / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1"
    a = root / "data/interim/occto/public_record_annotation/20261016_diagnostic_A_v1"
    catalog = json.loads((root / "metadata/artifact_catalog.json").read_text())["artifacts"]
    plan_path = next(root / x["path"] for x in catalog if "occto_reserve_nextday2_" in x["path"])
    business_path = next(
        root / x["path"] for x in catalog if "observed_unit_business_evidence_" in x["path"]
    )
    paths = [
        plan_path,
        business_path,
        old / "network_candidate_bounds.parquet",
        old / "regional_all_suppliers.parquet",
        a / "generation_with_snapshot_public_records_FY2025.parquet",
        root / "metadata/acquisition_manifest.jsonl",
        root / "metadata/data_sources.yml",
        root / "metadata/research/interconnector_direction_scope_20261016.csv",
    ]
    inputs = [{"path": str(p.relative_to(root)), "sha256": digest(p)} for p in paths]
    output.mkdir(parents=True)
    plan = pq.read_table(plan_path)
    mask = pc.is_in(plan["area"], value_set=pa.array(AREAS))
    nine = plan.filter(mask)
    frame = nine.select(["area", "delivery_date", "period"]).to_pandas()
    if len(frame) != 157680 or frame.duplicated(["area", "delivery_date", "period"]).any():
        raise ValueError("市場地域キー")
    if not frame.groupby(["area", "delivery_date"]).size().eq(48).all():
        raise ValueError("48コマ不一致")
    ledger = nine
    for name in ["fixed_output_forecast_mw", "net_demand_forecast_mw"]:
        ledger = ledger.append_column(name, pa.nulls(len(nine), type=pa.decimal128(28, 6)))
    ledger = ledger.append_column("net_demand_nonpositive", pa.nulls(len(nine), type=pa.bool_()))
    for name, value in {
        "fixed_output_status": "held_same_stage_fixed_output_not_in_catalog",
        "demand_boundary_status": (
            "official_definition_supported_FY2025_all_area_boundary_unverified"
        ),
        "capacity_overlap_status": "held_unknown_fuels_and_R_scope",
        "fixed_output_entity_status": "unresolved_not_assigned",
        "gross_net_balance_status": "held_R_unknown_not_zero",
    }.items():
        ledger = ledger.append_column(name, pa.array([value] * len(nine)))
    pq.write_table(ledger, output / "net_demand_input_ledger_FY2025.parquet", compression="zstd")
    if not pq.read_table(
        output / "net_demand_input_ledger_FY2025.parquet", columns=plan.column_names
    ).equals(nine):
        raise ValueError("元の需要列・型変更")
    # 原燃種のみを使用。設備同一性や新たな法人帰属の推測をしない。
    t = pq.read_table(
        paths[4],
        columns=[
            "asset_key_candidate",
            "area",
            "発電方式・燃種",
            "generation_kwh_30min",
            "capacity_reference_kw",
        ],
    )
    fuel = (
        t.group_by(["asset_key_candidate", "area", "発電方式・燃種"])
        .aggregate([("generation_kwh_30min", "sum"), ("generation_kwh_30min", "count")])
        .to_pandas()
    )
    if len(fuel) != 491 or fuel.asset_key_candidate.duplicated().any():
        raise ValueError("unit/燃種の一意性")
    business = pq.read_table(
        business_path,
        columns=[
            "asset_key_candidate",
            "hjks_business_name_unique_reference",
            "hjks_business_name_reference_status",
            "fy2025_asset_business_relationship_verified",
        ],
    ).to_pandas()
    fuel = fuel.merge(
        business, on="asset_key_candidate", how="left", validate="one_to_one", indicator=True
    )
    if not fuel._merge.eq("both").all():
        raise ValueError("事業者記載表の未対応")
    fuel["overlap_review"] = np.where(
        fuel["発電方式・燃種"].isin(["", "その他"]),
        "held_fuel_unknown_no_inference",
        "explicit_raw_fuel_only_R_scope_not_selected",
    )
    fuel.to_csv(report / "fixed_supply_overlap_inventory.csv", index=False)
    fuel.groupby("発電方式・燃種", dropna=False).agg(
        labels=("asset_key_candidate", "size"),
        observed_generation_kwh=("generation_kwh_30min_sum", "sum"),
    ).to_csv(report / "raw_fuel_summary.csv")
    # 旧版の全停止条件を保持し、全9地域で数値を定義できる時刻を検査。
    supply = pq.read_table(
        old / "regional_all_suppliers.parquet",
        columns=[
            "area",
            "delivery_date",
            "period",
            "supply_scenario",
            "adjusted_upper_if_observed_scope_defined_mw",
            "capacity_held_count",
            "record_held_count",
        ],
    ).to_pandas()
    supply = supply[supply.area.isin(AREAS)]
    ready_rows = []
    for scenario, group in supply.groupby("supply_scenario", sort=False):
        complete = (
            group.groupby(["delivery_date", "period"])
            .adjusted_upper_if_observed_scope_defined_mw.count()
            .eq(9)
        )
        ready_rows.append(
            dict(
                scenario=scenario,
                nine_area_times=17520,
                reference_scope_fully_defined_times=int(complete.sum()),
                fixed_output_defined_times=0,
                real_all_firm_lp_runs=0,
                reason="fixed_output_unknown; reference_scope_unknown_kept_null; cutover_scope",
            )
        )
    csv_write(report / "all_firm_execution_readiness.csv", ready_rows)
    # 元のネットワーク全列を保持。追加の定義を別表として作成する。
    net = pq.read_table(old / "network_candidate_bounds.parquet")
    f = net.to_pandas()
    keys = ["delivery_date", "period", "link_name_raw", "direction_raw"]
    if f.duplicated(keys).any():
        raise ValueError("系統キー重複")
    residual = (
        f.available_capacity_mw
        + f.planned_flow_mw
        - (f.operating_capacity_mw - f.margin_mw - f.wide_area_adjustment_mw)
    )
    if any(v != 0 for v in residual):
        raise ValueError("空容量と包絡の原値恒等式不一致")
    pivot = f.pivot(
        index=["delivery_date", "period"],
        columns=["link_name_raw", "direction_raw"],
        values="planned_flow_mw",
    )
    formula_checks = []
    for direction, opposite in [("順方向", "逆方向"), ("逆方向", "順方向")]:
        a_flow = pivot[("南福光連系所・南福光変電所の連系設備", direction)]
        b_flow = pivot[("越前嶺南線", opposite)]
        error = pivot[("北陸フェンス", direction)] - a_flow - b_flow
        if error.isna().any() or any(v != 0 for v in error):
            raise ValueError("北陸フェンス原潮流恒等式不一致")
        formula_checks.append(
            dict(
                direction=direction,
                keys=len(error),
                mismatches=0,
                nonzero_operand_rows=int((a_flow.ne(0) | b_flow.ne(0)).sum()),
                empirical_coefficient_identification=False,
                formula="f_hokuriku=f_fukumitsu−f_echizen (each forward-positive signed axis)",
            )
        )
    csv_write(report / "network_formula_checks.csv", formula_checks)
    constraints = []
    time_status = []
    for (day, period), group in f.groupby(["delivery_date", "period"], sort=True):
        end = group.timestamp_end.iloc[0]
        compiled = compile_legacy_constraints(group.to_dict("records"), end)
        time_status.append(
            dict(
                delivery_date=str(day),
                period=int(period),
                timestamp_end=str(end),
                network_structure_status=compiled["status"],
                physical_power_flow_verified=False,
                real_all_firm_lp_run=False,
                net_demand_lp_status="held_R_and_supply_accounting",
            )
        )
        for c in compiled["constraints"]:
            constraints.append(
                dict(
                    delivery_date=day,
                    period=int(period),
                    timestamp_end=end,
                    constraint_name=c["name"],
                    coefficients_json=json.dumps(c["coefficients"]),
                    lower_mw=c["lower_mw"],
                    upper_mw=c["upper_mw"],
                    source_ids_json=json.dumps(c["source_ids"]),
                    bound_role=c["bound_role"],
                    coefficient_status="official_structure_verified",
                    period_basis="dated_saved_values_legacy_before_cutover_day_not_physical_history",
                )
            )
    pq.write_table(
        pa.Table.from_pylist(constraints),
        output / "legacy_network_constraints_FY2025.parquet",
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist(time_status),
        output / "network_time_readiness_FY2025.parquet",
        compression="zstd",
    )
    status_counts = (
        pd.Series([x["network_structure_status"] for x in time_status]).value_counts().to_dict()
    )
    # 将来Rが揃った時の会計/LP処理を、承認を要しない人工例で検証。
    contract = FixedOutputContract(True, True, True, True, frozenset({"R"}), frozenset({"g"}))
    cases = {
        "net_ratio": solve_fixed_output(
            {"A": 100},
            {"A": 20},
            {"A": 100},
            [],
            [],
            contract=contract,
            network_ready=True,
            target="A",
            scale_basis="net",
        ),
        "gross_ratio": solve_fixed_output(
            {"A": 100},
            {"A": 20},
            {"A": 100},
            [],
            [],
            contract=contract,
            network_ready=True,
            target="A",
            scale_basis="gross",
        ),
        "negative_net_demand_export": solve_fixed_output(
            {"A": 100, "B": 100},
            {"A": 120, "B": 0},
            {"A": 0, "B": 80},
            [("A", "B")],
            [{"coefficients": (1,), "lower_mw": -50, "upper_mw": 50}],
            contract=contract,
            network_ready=True,
        ),
        "role": "人工例のみ。Rを固定した実FY2025計算は0件。",
    }
    (report / "artificial_accounting_checks.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    for x in inputs:
        if digest(root / x["path"]) != x["sha256"]:
            raise ValueError("入力SHA変更")
    qa = dict(
        version="20261026_net_demand_network_v1",
        inputs=inputs,
        original_plan_columns_preserved=True,
        market_area_rows=len(nine),
        unused_okinawa_keys=len(plan) - len(nine),
        duplicate_market_keys=0,
        original_observation_labels=491,
        business_join_matched=491,
        business_join_unmatched=0,
        explicit_solar_wind_labels=int(
            fuel["発電方式・燃種"].isin(["太陽光", "風力", "太陽光・太陽熱"]).sum()
        ),
        fuel_unknown_or_other_labels=int(fuel["発電方式・燃種"].isin(["", "その他"]).sum()),
        missing_R_rows=len(nine),
        nonpositive_net_demand_rows=None,
        net_demand_ratio_computable_rows=0,
        raw_envelope_identity_rows=len(f),
        raw_envelope_identity_mismatches=0,
        raw_planned_flow_nonzero_rows=int(f.planned_flow_mw.ne(0).sum()),
        compiled_network_constraint_rows=len(constraints),
        network_time_status_counts=status_counts,
        real_all_firm_lp_runs=0,
        real_net_gross_balance_comparisons=0,
        supply_scope_complete=ready_rows,
        independent_firm_removal_run=False,
        R_replaced_with_zero=False,
        final_baseline_selected=False,
        outputs=[
            dict(
                path=str(p.relative_to(root)),
                sha256=digest(p),
                bytes=p.stat().st_size,
                rows=pq.ParquetFile(p).metadata.num_rows,
            )
            for p in sorted(output.glob("*.parquet"))
        ],
    )
    (report / "audit.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
    (root / "metadata/net_demand_network_manifest_20261026.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                k: qa[k]
                for k in [
                    "market_area_rows",
                    "missing_R_rows",
                    "compiled_network_constraint_rows",
                    "network_time_status_counts",
                    "real_all_firm_lp_runs",
                ]
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.output.resolve())
