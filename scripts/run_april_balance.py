"""4月B30の全法人LP。固定版を変更せず新規ディレクトリへ保存。"""

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.actual_net_demand import clock_keys, numeric_raw  # noqa: E402
from src.april_balance import (  # noqa: E402
    signed_storage_account,
    solve_balance,
    validate_regional_header,
)
from src.fixed_output_feasibility import decode_csv  # noqa: E402
from src.net_demand_network import AREAS, EDGES  # noqa: E402

KEY = ["area", "delivery_date", "period"]
FILTER = [("delivery_date", ">=", date(2025, 4, 1)), ("delivery_date", "<=", date(2025, 4, 30))]


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(root, output):
    if output.exists():
        raise ValueError("既存版へ上書きしない")
    output.mkdir(parents=True)
    qa_dir = root / "reports/20_april_all_supplier_balance/qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    inputs, outputs = {}, []

    def read(path, **kwargs):
        inputs[str(path.relative_to(root))] = sha(path)
        return pq.read_table(path, **kwargs).to_pandas()

    def write(frame, name):
        path = output / (name + ".parquet")
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, path, compression="zstd")
        if not pq.read_table(path).equals(table):
            raise ValueError("Parquet再読込不一致")
        outputs.append(
            {"path": str(path.relative_to(root)), "sha256": sha(path), "rows": len(frame)}
        )

    old = read(
        root
        / "data/interim/occto/actual_net_demand/20261026_preparation_v7"
        / "actual_sample_and_plan_candidates.parquet"
    )
    archive_path = (
        root / "data/interim/research_reference/20261026_april_balance_v1/eria_jukyu_2025.zip"
    )
    inputs[str(archive_path.relative_to(root))] = sha(archive_path)
    member = "eria_jukyu_202504_04.csv"
    with zipfile.ZipFile(archive_path) as z:
        rows, encoding = decode_csv(z.read(member))
        archive_names = z.namelist()
    raw = pd.DataFrame(rows[2:], columns=rows[1])
    if len(raw) != 1440 or not rows[0][0].startswith("単位[MW平均]"):
        raise ValueError("中部燃料別単位/範囲不一致")
    chubu = pd.concat([raw, clock_keys(raw.DATE, raw.TIME, role="start")], axis=1)
    chubu["area"] = "中部"
    chubu["source_file"] = str(archive_path.relative_to(root))
    chubu["source_sha256"] = sha(archive_path)
    chubu["source_zip_member"] = member
    chubu["source_row"] = np.arange(len(chubu)) + 3
    for target, source in {
        "demand_actual_sample_mw": "エリア需要",
        "pv_actual_sample_mw": "太陽光発電実績",
        "wind_actual_sample_mw": "風力発電実績",
        "nuclear_regional_sample_mw": "原子力",
        "pv_curtailment_sample_mw": "太陽光出力制御量",
        "wind_curtailment_sample_mw": "風力出力制御量",
    }.items():
        chubu[target] = chubu[source].map(numeric_raw)
    chubu["renewable_actual_sample_mw"] = chubu.pv_actual_sample_mw + chubu.wind_actual_sample_mw
    write(chubu, "chubu_april_primary_all_original_columns")
    comparison = old[old.area.eq("中部")][
        KEY + ["demand_actual_sample_mw", "renewable_actual_sample_mw"]
    ].merge(
        chubu[KEY + ["demand_actual_sample_mw", "renewable_actual_sample_mw"]],
        on=KEY,
        suffixes=("_previous_keito", "_fuel_primary"),
        validate="one_to_one",
        indicator=True,
    )
    comparison.to_csv(qa_dir / "chubu_source_comparison.csv", index=False)
    data = pd.concat([old[~old.area.eq("中部")], chubu], ignore_index=True)
    if len(data) != 12960 or data.duplicated(KEY).any():
        raise ValueError("地域キー不一致")
    if (
        not data.groupby(["area", "delivery_date"])
        .period.apply(lambda x: set(x) == set(range(1, 49)))
        .all()
    ):
        raise ValueError("48コマ欠損")
    data["fixed_actual_mw"] = data.renewable_actual_sample_mw + data.nuclear_regional_sample_mw
    data["net_demand_B_mw"] = data.demand_actual_sample_mw - data.fixed_actual_mw
    # 元揚水/蓄電池は符号付き純値。充放電双方の総量へ分解しない。
    for col, raw_col in [
        ("pumped_net_injection_mw", "揚水"),
        ("battery_net_injection_mw", "蓄電池"),
    ]:
        data[col] = data[raw_col].map(lambda x: numeric_raw(x) if pd.notna(x) else None)
    data["storage_net_injection_mw"] = [
        signed_storage_account(p, b)
        for p, b in zip(data.pumped_net_injection_mw, data.battery_net_injection_mw, strict=True)
    ]
    data["net_import_from_actual_csv_mw"] = [
        None if pd.isna(v) else numeric_raw(v) * (-1 if a == "九州" else 1)
        for a, v in zip(data.area, data["連系線"], strict=True)
    ]
    data["demand_minus_storage_net_mw"] = [
        None if s is None else d - s
        for d, s in zip(data.demand_actual_sample_mw, data.storage_net_injection_mw, strict=True)
    ]
    data["accounting_scenario"] = "published_demand_only_no_observed_charging_obligation"
    data["gross_charging_reconstructed"] = False
    # 同じCSVの公表合計と需要を検算。総充電を復元しない。
    data["published_total_minus_demand_mw"] = [
        numeric_raw(v) - d if pd.notna(v) and str(v).strip() else None
        for v, d in zip(data["合計"], data.demand_actual_sample_mw, strict=True)
    ]
    write(data, "B30_primary_input_ledger")
    data.groupby("area").agg(
        rows=("period", "size"),
        unknown_storage_net=("storage_net_injection_mw", lambda x: int(x.isna().sum())),
        negative_pumped_net=("pumped_net_injection_mw", lambda x: int((x.dropna() < 0).sum())),
        negative_battery_net=("battery_net_injection_mw", lambda x: int((x.dropna() < 0).sum())),
        nonpositive_net=("net_demand_B_mw", lambda x: int((x.dropna() <= 0).sum())),
        max_absolute_total_residual=(
            "published_total_minus_demand_mw",
            lambda x: x.dropna().abs().max(),
        ),
    ).reset_index().to_csv(qa_dir / "storage_boundary_counts.csv", index=False)
    role_path = root / "reports/19_actual_net_demand/qa/fixed_adjustable_role_candidates.csv"
    inputs[str(role_path.relative_to(root))] = sha(role_path)
    roles = pd.read_csv(role_path)
    adjust = set(
        roles.loc[
            roles.candidate_analysis_role.eq("adjustable_reference_candidate")
            & roles.area.isin(AREAS),
            "asset_key_candidate",
        ]
    )
    fixed = set(
        roles.loc[
            roles.candidate_analysis_role.eq("fixed_observed_nuclear_candidate"),
            "asset_key_candidate",
        ]
    )
    assert len(adjust) == 372 and not (adjust & fixed)
    network = read(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet",
        filters=FILTER,
    )
    network.delivery_date = pd.to_datetime(network.delivery_date).dt.date
    if (
        len(network) != 15840
        or network.duplicated(["delivery_date", "period", "constraint_name"]).any()
    ):
        raise ValueError("9地域11制約の欠損/重複")
    network_groups = {
        (d, p): f for (d, p), f in network.groupby(["delivery_date", "period"], sort=True)
    }
    incidence = np.zeros((9, 10))
    for i, (_, sender, receiver) in enumerate(EDGES):
        incidence[AREAS.index(sender), i] = -1
        incidence[AREAS.index(receiver), i] = 1
    demand = data.set_index(KEY).sort_index()
    all_regional, results, solutions, diagnostics, unit_holds = [], [], [], [], []
    scenario_dir = (
        root / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1/unit_scenarios"
    )
    for path in sorted(scenario_dir.glob("*.parquet")):
        print("scenario", path.stem, flush=True)
        unit = read(path, filters=FILTER)
        if unit.duplicated(["asset_key_candidate", "delivery_date", "period"]).any():
            raise ValueError("元unitキー重複")
        subset = unit[unit.asset_key_candidate.isin(adjust)].copy()
        if subset.source_observation_row.duplicated().any():
            raise ValueError("元観測の増殖")
        subset["defined"] = (
            subset.adjusted_reference_upper_kw.notna()
            & subset.adjusted_reference_upper_kw.map(lambda v: v is not None and v >= 0)
        )
        for (asset, state), group in subset[~subset.defined].groupby(
            ["asset_key_candidate", "scenario_status"]
        ):
            unit_holds.append(
                {
                    "supply_scenario": path.stem,
                    "asset_key_candidate": asset,
                    "scenario_status": state,
                    "cells": len(group),
                }
            )
        regional = []
        for (area, day, period), group in subset.groupby(KEY, sort=True):
            known = group[group.defined]
            # 空集合の和=0は定義した限定集合だけ。未知設備の上限0とは別。
            upper = sum(known.adjusted_reference_upper_kw, Decimal(0)) / Decimal(1000)
            regional.append(
                {
                    "area": area,
                    "delivery_date": day,
                    "period": int(period),
                    "supply_scenario": path.stem,
                    "limited_known_upper_mw": upper,
                    "observed_candidate_count": len(group),
                    "defined_candidate_count": len(known),
                    "held_candidate_count": len(group) - len(known),
                    "known_reference_held_mw": sum(
                        group.loc[~group.defined, "capacity_reference_kw"].dropna(), Decimal(0)
                    )
                    / Decimal(1000),
                    "unknown_capacity_assumed_zero": False,
                    "generation_missing_but_upper_defined_count": int(
                        known.qa_generation_missing.sum()
                    ),
                }
            )
        reg = pd.DataFrame(regional)
        if len(reg) != 12960 or reg.duplicated(KEY).any():
            raise ValueError("地域入力欠落。空地域を推測で0補完しない")
        all_regional.append(reg)
        idx = reg.set_index(KEY)
        for (day, period), group in network_groups.items():
            order = [(a, day, period) for a in AREAS]
            frame = demand.loc[order]
            net = frame.net_demand_B_mw.to_numpy(dtype=float)
            upper = idx.loc[order].limited_known_upper_mw.to_numpy(dtype=float)
            coeff = np.array([json.loads(c) for c in group.coefficients_json], float)
            result = solve_balance(
                net,
                upper,
                incidence,
                coeff,
                group.lower_mw.to_numpy(float),
                group.upper_mw.to_numpy(float),
            )
            base = {"supply_scenario": path.stem, "delivery_date": day, "period": int(period)}
            record = {
                **base,
                **{k: v for k, v in result.items() if not isinstance(v, list)},
                "total_net_demand_mw": float(net.sum()),
                "limited_upper_total_mw": float(upper.sum()),
                "held_candidate_cells": int(idx.loc[order].held_candidate_count.sum()),
                "network_constraint_count": len(group),
                "firm_removal_applied": False,
            }
            results.append(record)
            if result["status"] == "feasible":
                solutions.append(
                    {
                        **base,
                        "generation_mw_by_area_json": json.dumps(result["generation_mw"]),
                        "flow_mw_in_EDGES_order_json": json.dumps(result["flows_mw"]),
                    }
                )
            elif result["status"] == "infeasible":
                for i, a in enumerate(AREAS):
                    diagnostics.append(
                        {
                            **base,
                            "area": a,
                            "diagnostic_only": True,
                            "deficit_slack_mw": result["diagnostic_deficit_mw"][i],
                            "surplus_slack_mw": result["diagnostic_surplus_mw"][i],
                        }
                    )
    reg = pd.concat(all_regional, ignore_index=True)
    result_frame = pd.DataFrame(results)
    if (
        len(result_frame) != 12960
        or result_frame.duplicated(["supply_scenario", "delivery_date", "period"]).any()
    ):
        raise ValueError("実行件数/キー不一致")
    write(reg, "limited_supply_regional_inputs")
    write(result_frame, "all_firm_balance_results")
    write(pd.DataFrame(solutions), "feasible_allocations")
    write(pd.DataFrame(diagnostics), "diagnostic_only_balance_slacks")
    pd.DataFrame(unit_holds).to_csv(qa_dir / "unit_input_holds.csv", index=False)
    counts = result_frame.groupby(["supply_scenario", "status"]).size().unstack(fill_value=0)
    counts.to_csv(qa_dir / "run_counts.csv")
    result_frame[result_frame.status.eq("infeasible")].groupby("supply_scenario").agg(
        cases=("period", "size"),
        network_relaxed_feasible=("network_relaxed_feasible", "sum"),
        maximum_slack_mw=("diagnostic_slack_sum_mw", "max"),
        maximum_national_upper_shortfall_mw=("national_upper_shortfall_mw", "max"),
        maximum_national_fixed_surplus_mw=("national_fixed_surplus_mw", "max"),
    ).to_csv(qa_dir / "infeasibility_summary.csv")
    examples = (
        result_frame.sort_values("diagnostic_slack_sum_mw", ascending=False)
        .groupby("supply_scenario")
        .head(2)
    )
    examples.to_csv(qa_dir / "representative_cases.csv", index=False)
    reg.groupby(["supply_scenario", "delivery_date", "period"]).agg(
        defined=("defined_candidate_count", "sum"),
        held=("held_candidate_count", "sum"),
        held_ref=("known_reference_held_mw", "sum"),
        u=("limited_known_upper_mw", "sum"),
    ).groupby("supply_scenario").agg(["min", "max"]).to_csv(qa_dir / "limited_scope_summary.csv")
    pd.DataFrame(diagnostics).groupby(["supply_scenario", "area"]).agg(
        deficit_cells=("deficit_slack_mw", lambda x: int((x > 1e-6).sum())),
        surplus_cells=("surplus_slack_mw", lambda x: int((x > 1e-6).sum())),
        max_deficit_mw=("deficit_slack_mw", "max"),
        max_surplus_mw=("surplus_slack_mw", "max"),
    ).to_csv(qa_dir / "slack_area_summary.csv")
    # 地域別REIは全9件を検査し、不一致は入力から拒否。一次資料LPへ影響させない。
    review = []
    for area, roman in zip(
        AREAS,
        (
            "hokkaido",
            "tohoku",
            "tokyo",
            "chubu",
            "hokuriku",
            "kansai",
            "chugoku",
            "shikoku",
            "kyushu",
        ),
        strict=True,
    ):
        path = root / f"data/raw/rei/supply_demand/rei_supply_demand_{roman}_FY2025.csv"
        rows, _ = decode_csv(path.read_bytes())
        inputs[str(path.relative_to(root))] = sha(path)
        try:
            validate_regional_header(area, rows[0][2:])
            status = "header_verified"
        except ValueError:
            status = "rejected_filename_header_mismatch"
        review.append({"area": area, "status": status, "source_path": str(path.relative_to(root))})
    pd.DataFrame(review).to_csv(qa_dir / "rei_input_guard.csv", index=False)
    for relative, digest in inputs.items():
        if sha(root / relative) != digest:
            raise ValueError("入力原本変更")
    audit = {
        "version": "20261026_B30_april_v1",
        "rows": len(result_frame),
        "primary_key_rows": len(data),
        "primary_null_D_R_N": {
            k: int(data[k].isna().sum())
            for k in ["demand_actual_sample_mw", "fixed_actual_mw", "net_demand_B_mw"]
        },
        "source_encoding": encoding,
        "annual_zip_members": archive_names,
        "only_inspected_member": member,
        "adjustable_labels_nine_areas": len(adjust),
        "fixed_nuclear_labels": len(fixed),
        "fixed_adjustable_overlap": 0,
        "firm_removal_applied": False,
        "primary_join_matches": 12960,
        "primary_join_unmatched": 0,
        "network_join_matches": 12960,
        "network_join_unmatched": 0,
        "upper_scope": "defined_nonnegative_subset_not_zero_imputation",
        "inputs": [{"path": p, "sha256": s} for p, s in inputs.items()],
        "outputs": outputs,
        "fixed_version_changed": False,
        "tolerance_mw": 1e-6,
    }
    (qa_dir / "run_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(counts.to_string(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.data_root.resolve(), args.output.resolve())
