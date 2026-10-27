"""工程23の不成立を、既存のL1過不足診断で切り分ける。正式供給へ混ぜない。"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.diagnose_april_constraints import FOCUS, SOURCE, sha  # noqa: E402
from src.april_constraint_diagnosis import diagnostic_slack  # noqa: E402
from src.net_demand_network import AREAS, EDGES  # noqa: E402


def run(root, output, report):
    target = output / "focus_diagnostic_slacks_all_infeasible.parquet"
    if target.exists():
        raise ValueError("既存診断を上書きしない")
    source = output / "simultaneous_input_scope_diagnosis.parquet"
    d = pd.read_parquet(source)
    d = d[
        d.network_scenario.eq("saved_plan_constraints")
        & d.entity_code.isin(FOCUS)
        & d.removed_simultaneous_status.eq("infeasible")
    ]
    inputs = {}

    def read(p, **kw):
        inputs[str(p.relative_to(root))] = sha(p)
        return pq.read_table(p, **kw).to_pandas()

    regional = read(root / SOURCE / "regional_trial_uppers.parquet").set_index(
        ["supply_trial_scenario", "area", "delivery_date", "period"]
    )
    primary = read(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    ).set_index(["area", "delivery_date", "period"])
    network = read(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet",
        filters=[
            ("delivery_date", ">=", date(2025, 4, 1)),
            ("delivery_date", "<=", date(2025, 4, 30)),
        ],
    )
    ng = {(day, p): g for (day, p), g in network.groupby(["delivery_date", "period"])}
    inc = np.zeros((9, 10))
    for j, (_, s, t) in enumerate(EDGES):
        inc[AREAS.index(s), j] = -1
        inc[AREAS.index(t), j] = 1
    rows = []
    cache = {}
    for case, group in d.groupby("supply_trial_scenario"):
        print("diagnostic slacks", case, flush=True)
        for row in group.itertuples():
            day, p, code = row.delivery_date, row.period, row.entity_code
            n = primary.loc[[(a, day, p) for a in AREAS]].net_demand_B_mw.to_numpy(float)
            g = regional.loc[[(case, a, day, p) for a in AREAS]]
            up = (g.upper_limited_mw - g["remove_" + code + "_mw"]).to_numpy(float)
            network = ng[day, p]
            c = np.array([json.loads(x) for x in network.coefficients_json])
            lo = network.lower_mw.to_numpy(float)
            hi = network.upper_mw.to_numpy(float)
            key = (tuple(n), tuple(up), tuple(lo), tuple(hi))
            if key not in cache:
                cache[key] = diagnostic_slack(n, up, inc, c, lo, hi)
            result = cache[key]
            deficit = sum(result["diagnostic_deficit_mw"])
            surplus = sum(result["diagnostic_surplus_mw"])
            if row.national_known_upper_shortfall_mw > 1e-6:
                kind = "national_limited_upper_shortfall"
            elif row.national_fixed_surplus_mw > 1e-6:
                kind = "national_fixed_output_absorption_shortfall"
            elif surplus > 1e-6 and deficit > 1e-6:
                kind = "regional_deficit_and_local_absorption"
            elif surplus > 1e-6:
                kind = "local_absorption_or_export_restriction"
            else:
                kind = "regional_delivery_restriction"
            rows.append(
                dict(
                    supply_trial_scenario=case,
                    entity_code=code,
                    delivery_date=day,
                    period=int(p),
                    all_firms_status=row.all_firms_status,
                    diagnostic_kind=kind,
                    diagnostic_deficit_total_mw=deficit,
                    diagnostic_surplus_total_mw=surplus,
                    diagnostic_slack_sum_mw=result["diagnostic_slack_sum_mw"],
                    diagnostic_deficit_by_area_json=json.dumps(result["diagnostic_deficit_mw"]),
                    diagnostic_surplus_by_area_json=json.dumps(result["diagnostic_surplus_mw"]),
                    diagnostic_residual_mw=result["diagnostic_residual_mw"],
                )
            )
    f = pd.DataFrame(rows)
    t = pa.Table.from_pandas(f, preserve_index=False)
    pq.write_table(t, target, compression="zstd")
    assert pq.read_table(target).equals(t)
    q = report / "qa"
    f.groupby(
        ["supply_trial_scenario", "entity_code", "all_firms_status", "diagnostic_kind"]
    ).size().rename("time_keys").reset_index().to_csv(
        q / "focus_slack_diagnosis_counts.csv", index=False
    )
    for p in [output / "manifest.json", q / "run_audit.json"]:
        m = json.loads(p.read_text())
        m["outputs"].append(
            {"path": str(target.relative_to(root)), "sha256": sha(target), "rows": len(f)}
        )
        m["slack_unique_lp_calls"] = len(cache)
        m["slack_frames"] = len(f)
        for path, h in inputs.items():
            assert sha(root / path) == h
            assert any(x["path"] == path and x["sha256"] == h for x in m["inputs"])
        p.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n")
    print("slacks complete", len(f), "unique", len(cache), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    run(Path(__file__).resolve().parents[1], a.output.resolve(), a.report.resolve())
