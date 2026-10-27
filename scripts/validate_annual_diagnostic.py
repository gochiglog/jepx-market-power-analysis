"""年間の原本・旧固定版・実LPを独立照合。旧報告へ書き込まない。"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_annual_actuals import decode_csv
from scripts.run_annual_diagnostic import OUT, ROOT
from src.actual_net_demand import clock_keys, numeric_raw, regional_column_indices
from src.adjustable_removal import maximum_multiplier
from src.annual_supply_solver import A, C
from src.april_balance import solve_balance
from src.net_demand_network import AREAS

QA = ROOT / "reports/27_fy2025_annual/qa"
PRIMARY = (
    ROOT
    / "data/interim/occto/annual_diagnostic/20261027_inputs_v2/annual_primary_input_ledger.parquet"
)


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def boundaries():
    f = pq.read_table(PRIMARY).to_pandas()
    f["month"] = pd.to_datetime(f.delivery_date).dt.strftime("%Y%m")
    f["total_residual"] = [
        numeric_raw(x) - d if pd.notna(x) and str(x).strip() else None
        for x, d in zip(f["合計"], f.demand_actual_sample_mw)
    ]
    rows = []
    for (area, month), g in f.groupby(["area", "month"]):
        rows.append(
            dict(
                area=area,
                month=month,
                frames=len(g),
                negative_pump_net=int(g.pumped_net_injection_mw.dropna().lt(0).sum()),
                negative_battery_net=int(g.battery_net_injection_mw.dropna().lt(0).sum()),
                unknown_pump=int(g.pumped_net_injection_mw.isna().sum()),
                unknown_battery=int(g.battery_net_injection_mw.isna().sum()),
                nonpositive_net=int(g.net_demand_B_mw.le(0).sum()),
                total_residual_max_abs=float(g.total_residual.dropna().abs().max()),
                curtailment_not_added_to_R=True,
                gross_charging_reconstructed=False,
            )
        )
    pd.DataFrame(rows).to_csv(QA / "annual_accounting_boundary_counts.csv", index=False)
    rows, _ = decode_csv((ROOT / "data/raw/rei/power_data/rei_power_data_FY2025.csv").read_bytes())
    raw = pd.DataFrame(rows[2:], columns=[f"raw_{i}" for i in range(len(rows[1]))])
    keys = clock_keys(raw.raw_0, raw.raw_1, role="start")[["delivery_date", "period"]]
    checks = []
    for area in AREAS:
        indices = regional_column_indices(
            rows[0], rows[1], area, ["需要", "太陽光", "風力", "原子力"]
        )
        rei = keys.copy()
        for label, i in indices.items():
            rei["rei__" + label] = raw[f"raw_{i}"].map(numeric_raw)
        joined = f[f.area.eq(area)].merge(
            rei, on=["delivery_date", "period"], how="left", validate="one_to_one", indicator=True
        )
        assert joined._merge.eq("both").all()
        for col, label in [
            ("demand_actual_sample_mw", "需要"),
            ("pv_actual_sample_mw", "太陽光"),
            ("wind_actual_sample_mw", "風力"),
            ("nuclear_regional_sample_mw", "原子力"),
        ]:
            known = joined[col].notna() & joined["rei__" + label].notna()
            diff = joined.loc[known, col] - joined.loc[known, "rei__" + label]
            checks.append(
                dict(
                    area=area,
                    variable=col,
                    joined=len(joined),
                    unmatched=0,
                    comparable=int(known.sum()),
                    different=int(diff.ne(0).sum()),
                    max_abs_difference_mw=float(diff.abs().max()),
                    primary_replaced=False,
                )
            )
    pd.DataFrame(checks).to_csv(QA / "annual_primary_vs_rei.csv", index=False)


def independent():
    count = 0
    maxerr = 0
    residual = 0
    for path in sorted((OUT / "samples").glob("*.parquet")):
        for r in pq.read_table(path).to_pylist():
            n, u, lo, hi = [
                np.array(json.loads(r[k]))
                for k in ["net_mw_json", "upper_mw_json", "lower_mw_json", "ceiling_mw_json"]
            ]
            active = np.isfinite(lo) & np.isfinite(hi)
            target = AREAS.index(r["target_area"])
            answer = maximum_multiplier(n, u, target, A, C[active], lo[active], hi[active])
            assert answer["status"] == "valid", r
            err = abs(answer["multiplier"] - r["multiplier"])
            assert np.isclose(answer["multiplier"], r["multiplier"], rtol=1e-9, atol=1e-7), r
            maxerr = max(maxerr, err)
            residual = max(residual, r["residual_mw"])
            count += 1
    # 企業除去・全法人の成立/不成立も月初の固定規則で別LPへ。
    primary = pq.read_table(PRIMARY).to_pandas().set_index(["area", "delivery_date", "period"])
    network = pq.read_table(
        ROOT
        / "data/interim/occto/annual_diagnostic/20261027_network_v1"
        / "annual_network_constraints.parquet"
    ).to_pandas()
    network = network.set_index(["delivery_date", "period"])
    simcheck = 0
    infeasible = 0
    paths = sorted((OUT / "simultaneous").glob("*__reference_no_deduction.parquet"))
    for path in paths:
        f = pd.read_parquet(path)
        f = f[f.network_scenario.eq("saved_plan_constraints")]
        # 各月4/1と同じ日内規則＋最後のコマ。恣意的な価格選択なし。
        chosen = f[
            (f.delivery_date.eq(f.delivery_date.min()) & f.period.isin([1, 24, 48]))
            | (f.delivery_date.eq(f.delivery_date.max()) & f.period.eq(48))
        ]
        reg = (
            pq.read_table(
                ROOT
                / "data/interim/occto/annual_diagnostic/20261027_supply_v2/regional"
                / "reference_no_deduction.parquet"
            )
            .to_pandas()
            .set_index(["area", "delivery_date", "period"])
        )
        for r in chosen.itertuples():
            if r.all_firms_status == "held_network_input":
                continue
            n = np.array(
                [
                    float(primary.loc[(a, r.delivery_date, r.period), "net_demand_B_mw"])
                    for a in AREAS
                ]
            )
            u = np.array(
                [
                    float(reg.loc[(a, r.delivery_date, r.period), "upper_limited_mw"])
                    - float(
                        reg.loc[(a, r.delivery_date, r.period), "remove_" + r.entity_code + "_mw"]
                    )
                    for a in AREAS
                ]
            )
            ng = network.loc[(r.delivery_date, r.period)]
            cc = np.array([json.loads(x) for x in ng.coefficients_json])
            lo = ng.lower_mw.astype(float).to_numpy()
            hi = ng.upper_mw.astype(float).to_numpy()
            answer = solve_balance(n, u, A, cc, lo, hi)
            assert answer["status"] == r.removed_simultaneous_status, (r, answer)
            simcheck += 1
            infeasible += int(answer["status"] == "infeasible")
    # q定式化とlambda定式化が年度切替を含む標本で一致。
    return dict(
        independent_multiplier_samples=count,
        maximum_absolute_multiplier_difference=maxerr,
        maximum_saved_solution_residual_mw=residual,
        independent_simultaneous_samples=simcheck,
        infeasible_samples=infeasible,
    )


def april():
    directory = ROOT / "data/interim/occto/central_spec_audit/20261027_two_asset_v2"
    old = pd.read_parquet(directory / "hokkaido_multiplier_extension_all_times.parquet")
    oldsim = pd.read_parquet(directory / "nine_area_simultaneous_extension_all_times.parquet")
    current = pd.concat(
        [pd.read_parquet(p) for p in sorted((OUT / "multipliers").glob("202504__*.parquet"))],
        ignore_index=True,
    )
    current = current[current.target_area.eq("北海道")]
    keys = ["supply_trial_scenario", "network_scenario", "delivery_date", "period", "entity_code"]
    joined = old.merge(
        current,
        on=keys,
        how="outer",
        validate="one_to_one",
        indicator=True,
        suffixes=("_old", "_annual"),
    )
    assert joined._merge.eq("both").all()
    assert joined.status_old.equals(joined.status_annual)
    valid = joined.status_old.eq("valid")
    error = float(
        abs(
            joined.loc[valid, "lambda_removed_old"] - joined.loc[valid, "lambda_removed_annual"]
        ).max()
    )
    assert error < 1e-7
    current_sim = pd.concat(
        [pd.read_parquet(p) for p in sorted((OUT / "simultaneous").glob("202504__*.parquet"))],
        ignore_index=True,
    )
    sj = oldsim.merge(
        current_sim,
        on=keys,
        how="outer",
        validate="one_to_one",
        indicator=True,
        suffixes=("_old", "_annual"),
    )
    assert sj._merge.eq("both").all()
    for col in ["all_firms_status", "removed_simultaneous_status"]:
        assert sj[col + "_old"].equals(sj[col + "_annual"]), col
    return dict(
        april_hokkaido_frames=len(joined),
        april_simultaneous_frames=len(sj),
        april_join_rate=1.0,
        april_statuses_equal=True,
        april_multiplier_max_absolute_difference=error,
    )


def hashes():
    checked = {}

    def check(path, value):
        if path in checked:
            return
        assert sha(ROOT / path) == value, path
        checked[path] = value

    for line in (ROOT / "metadata/acquisition_manifest.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["status"] in ["downloaded", "imported"]:
            check(r["local_path"], r["sha256"])
    for r in json.loads(
        (ROOT / "metadata/research/asset_reference_manifest_20261016.json").read_text()
    ):
        check(r["local_path"], r["sha256"])

    def walk(obj):
        if isinstance(obj, list):
            for x in obj:
                walk(x)
        elif isinstance(obj, dict):
            if "sha256" in obj:
                p = obj.get("path", obj.get("local_path"))
                if p and (ROOT / p).is_file():
                    check(p, obj["sha256"])
            for k, v in obj.items():
                if k == "fixed_file_hashes" and isinstance(v, dict):
                    for p, h in v.items():
                        check(p, h)
                elif isinstance(v, (list, dict)):
                    walk(v)

    for name in [
        "saved_plan_supply_manifest_20261024.json",
        "net_demand_network_manifest_20261026.json",
        "adjustable_removal_manifest_20261027.json",
        "central_spec_audit_manifest_20261027.json",
        "paper_figure_plan_manifest_20261027.json",
    ]:
        walk(json.loads((ROOT / "metadata" / name).read_text()))
    walk(json.loads((ROOT / "reports/25_central_spec_audit/qa/verification.json").read_text()))
    for line in (
        (ROOT / "metadata/research/annual_actual_acquisition_20261027.jsonl")
        .read_text()
        .splitlines()
    ):
        walk(json.loads(line))
    (QA / "immutable_sha_audit.json").write_text(
        json.dumps(
            dict(
                verified_files=len(checked),
                hashes=checked,
                raw_changed=False,
                old_fixed_outputs_changed=False,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return len(checked)


def main():
    boundaries()
    result = independent()
    result.update(april())
    result["immutable_files_verified"] = hashes()
    records = [json.loads(p.read_text()) for p in sorted((OUT / "checkpoints").glob("*.json"))]
    result.update(
        months=len(records),
        multiplier_frames=sum(r["multiplier_frames"] for r in records),
        simultaneous_frames=sum(r["simultaneous_frames"] for r in records),
        actual_solver_calls=sum(r["solver_calls"] for r in records),
        identical_input_cache_reuses=sum(r["identical_input_cache_reuses"] for r in records),
    )
    (QA / "annual_independent_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(result, flush=True)


if __name__ == "__main__":
    main()
