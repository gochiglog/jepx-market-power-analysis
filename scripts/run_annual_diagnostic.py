"""月単位に再開する年間17条件・同時成立/地域倍率。元需要と企業除去順序を維持。"""

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.trial_adjustable_removal import ENTITIES, TARGETS  # noqa: E402
from src.annual_supply_solver import CONSTRAINT_NAMES, TransportModel  # noqa: E402
from src.net_demand_network import AREAS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/interim/occto/annual_diagnostic/20261027_results_v1"
INPUT = (
    ROOT
    / "data/interim/occto/annual_diagnostic/20261027_inputs_v2/annual_primary_input_ledger.parquet"
)
SUPPLY = ROOT / "data/interim/occto/annual_diagnostic/20261027_supply_v2/regional"
NETWORK = ROOT / "data/interim/occto/annual_diagnostic/20261027_network_v1"


def month_work(month):
    start = time.monotonic()
    year, m = int(month[:4]), int(month[4:])
    first = date(year, m, 1)
    last = date(year + int(m == 12), m % 12 + 1, 1)
    checkpoint = OUT / "checkpoints" / (month + ".json")
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        for filename, expected in saved.get("output_sha256", {}).items():
            with (ROOT / filename).open("rb") as handle:
                assert hashlib.file_digest(handle, "sha256").hexdigest() == expected, filename
        return saved
    filters = [("delivery_date", ">=", first), ("delivery_date", "<", last)]
    primary = pq.read_table(INPUT, filters=filters).to_pandas()
    primary.delivery_date = pd.to_datetime(primary.delivery_date).dt.date
    times = sorted(set(zip(primary.delivery_date, primary.period)))
    primary = primary.set_index(["delivery_date", "period", "area"])
    n = np.array(
        [[float(primary.loc[(d, p, a), "net_demand_B_mw"]) for a in AREAS] for d, p in times]
    )
    networks = pq.read_table(
        NETWORK / "annual_network_constraints.parquet", filters=filters
    ).to_pandas()
    networks.delivery_date = pd.to_datetime(networks.delivery_date).dt.date
    bounds = {}
    for key, g in networks.groupby(["delivery_date", "period"]):
        lo = np.full(13, -np.inf)
        hi = np.full(13, np.inf)
        for r in g.itertuples():
            k = CONSTRAINT_NAMES.index(r.constraint_name)
            lo[k] = float(r.lower_mw)
            hi[k] = float(r.upper_mw)
        bounds[key] = (lo, hi)
    sim_model = TransportModel()
    maxmodels = [TransportModel(k) for k in range(9)]
    simcache = {}
    maxcache = {}
    samples = []
    sampled = set()
    reuse = 0

    def sim(n, u, lo, hi, constrained, base_status=None):
        nonlocal reuse
        if not np.isfinite(np.r_[n, u]).all():
            return "input_held"
        if (u < 0).any():
            return "negative_upper_held"
        # 除去で上限は増えない。元需要の全法人不成立なら除去後も不成立。
        if base_status == "infeasible":
            return "infeasible"
        if n.sum() < -1e-7 or n.sum() > u.sum() + 1e-7:
            return "infeasible"
        if not constrained:
            return "feasible"
        if lo is None:
            return "held_network_input"
        key = (n.tobytes(), u.tobytes(), lo.tobytes(), hi.tobytes())
        if key in simcache:
            reuse += 1
            return simcache[key]
        result = sim_model.solve(n, u, lo, hi)["status"]
        simcache[key] = result
        return result

    def maximum(n, u, target, lo, hi, constrained, scenario, d, p, kind):
        nonlocal reuse
        if not constrained:
            q = u.sum() - (n.sum() - n[target])
            return (
                ("valid", float(q / n[target]))
                if q >= 0
                else ("removed_other_regions_infeasible", None)
            )
        key = (n.tobytes(), u.tobytes(), lo.tobytes(), hi.tobytes(), target)
        if key in maxcache:
            reuse += 1
            return maxcache[key]
        result = maxmodels[target].solve(n, u, lo, hi)
        status = (
            "valid"
            if result["status"] == "feasible"
            else "removed_other_regions_infeasible"
            if result["status"] == "infeasible"
            else result["status"]
        )
        value = result["value"] / n[target] if status == "valid" else None
        maxcache[key] = (status, value)
        samplekey = (target, kind)
        if status == "valid" and samplekey not in sampled:
            samples.append(
                dict(
                    supply_trial_scenario=scenario,
                    delivery_date=d,
                    period=int(p),
                    target_area=AREAS[target],
                    kind=kind,
                    net_mw_json=json.dumps(n.tolist()),
                    upper_mw_json=json.dumps(u.tolist()),
                    lower_mw_json=json.dumps(lo.tolist()),
                    ceiling_mw_json=json.dumps(hi.tolist()),
                    generation_mw_json=json.dumps(result["generation_mw"]),
                    flows_mw_json=json.dumps(result["flows_mw"]),
                    multiplier=value,
                    residual_mw=result["residual_mw"],
                )
            )
            sampled.add(samplekey)
        return status, value

    written = []
    for path in sorted(SUPPLY.glob("*.parquet")):
        case = path.stem
        df = pq.read_table(path, filters=filters).to_pandas()
        df.delivery_date = pd.to_datetime(df.delivery_date).dt.date
        reg = df.set_index(["delivery_date", "period", "area"])
        u = np.array(
            [
                [
                    float(reg.loc[(d, p, a), "upper_limited_mw"])
                    if reg.loc[(d, p, a), "upper_limited_mw"] is not None
                    else np.nan
                    for a in AREAS
                ]
                for d, p in times
            ]
        )
        removal = {
            code: np.array(
                [
                    [float(reg.loc[(d, p, a), "remove_" + code + "_mw"]) for a in AREAS]
                    for d, p in times
                ]
            )
            for code in ENTITIES
        }
        counts = np.array(
            [[int(reg.loc[(d, p, a), "held_candidate_count"]) for a in AREAS] for d, p in times]
        ).sum(axis=1)
        simrows = []
        multrows = []
        base_rows = []
        for t, (d, p) in enumerate(times):
            known = (d, p) in bounds
            lo, hi = bounds.get((d, p), (None, None))
            for constrained, ns in [
                (True, "saved_plan_constraints"),
                (False, "no_transmission_bounds"),
            ]:
                # 必要なネットワーク入力がない場合、全国部分和の証明より先に保留。
                baseline = (
                    "held_network_input"
                    if constrained and not known
                    else sim(n[t], u[t], lo, hi, constrained)
                )
                base_rows.append(
                    dict(
                        supply_trial_scenario=case,
                        network_scenario=ns,
                        delivery_date=d,
                        period=int(p),
                        all_firms_status=baseline,
                        national_net_demand_mw=float(n[t].sum()),
                        national_known_upper_mw=float(u[t].sum())
                        if np.isfinite(u[t]).all()
                        else None,
                        held_candidate_cells=int(counts[t]),
                    )
                )
                removed_status = {}
                for code, entity in ENTITIES.items():
                    remaining = u[t] - removal[code][t]
                    result = (
                        "held_network_input"
                        if constrained and not known
                        else sim(n[t], remaining, lo, hi, constrained, baseline)
                    )
                    removed_status[code] = result
                    simrows.append(
                        dict(
                            supply_trial_scenario=case,
                            network_scenario=ns,
                            delivery_date=d,
                            period=int(p),
                            entity_code=code,
                            entity=entity,
                            all_firms_status=baseline,
                            removed_simultaneous_status=result,
                            removed_adjustable_mw_all_nine_areas=float(removal[code][t].sum()),
                            fixed_R_removed=False,
                            national_remaining_known_upper_mw=float(remaining.sum())
                            if np.isfinite(remaining).all()
                            else None,
                            national_net_demand_mw=float(n[t].sum()),
                            held_candidate_cells=int(counts[t]),
                        )
                    )
                for area, code in TARGETS:
                    target = AREAS.index(area)
                    status = "valid"
                    la = None
                    lr = None
                    if n[t, target] <= 0:
                        status = "nonpositive_target_net"
                    elif baseline == "infeasible":
                        status = "all_firms_infeasible"
                    elif baseline != "feasible":
                        status = baseline
                    else:
                        ast, la = maximum(
                            n[t], u[t], target, lo, hi, constrained, case, d, p, "all"
                        )
                        status, lr = maximum(
                            n[t],
                            u[t] - removal[code][t],
                            target,
                            lo,
                            hi,
                            constrained,
                            case,
                            d,
                            p,
                            "removed",
                        )
                        if ast != "valid":
                            status = "numerical_failure"
                    # 主statusとは別に元需要の独立判定を残す。
                    multrows.append(
                        dict(
                            supply_trial_scenario=case,
                            network_scenario=ns,
                            delivery_date=d,
                            period=int(p),
                            target_area=area,
                            entity_code=code,
                            entity=ENTITIES[code],
                            status=status,
                            lambda_all_firms=la,
                            lambda_removed=lr,
                            all_firms_balance_status=baseline,
                            removed_simultaneous_status=removed_status[code],
                            target_net_demand_mw=float(n[t, target]),
                            held_candidate_cells=int(counts[t]),
                            removed_adjustable_mw_all_nine_areas=float(removal[code][t].sum()),
                            removed_fixed_output_mw=0.0,
                        )
                    )
        for folder, rows in [
            ("simultaneous", simrows),
            ("multipliers", multrows),
            ("all_firms", base_rows),
        ]:
            dest = OUT / folder / (month + "__" + case + ".parquet")
            pd.DataFrame(rows).to_parquet(dest, index=False, compression="zstd")
            written.append(str(dest.relative_to(ROOT)))
        print(month, case, "written", len(multrows), flush=True)
    pd.DataFrame(samples).to_parquet(OUT / "samples" / (month + ".parquet"), index=False)
    record = dict(
        month=month,
        time_keys=len(times),
        supply_conditions=17,
        multiplier_frames=len(times) * 17 * 2 * 9,
        simultaneous_frames=len(times) * 17 * 2 * 8,
        solver_calls=sim_model.calls + sum(m.calls for m in maxmodels),
        feasibility_solver_calls=sim_model.calls,
        multiplier_solver_calls=sum(m.calls for m in maxmodels),
        identical_input_cache_reuses=reuse,
        numerical_samples=len(samples),
        outputs=written,
        elapsed_seconds=time.monotonic() - start,
        highspy_version=sim_model.h.version(),
    )
    checkpoint.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print("MONTH COMPLETE", month, record["solver_calls"], flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    # 固定版から再開するときは、実際に読む年間入力もSHA照合する。
    manifest = ROOT / "metadata/annual_diagnostic_manifest_20261027.json"
    if manifest.exists():
        frozen = json.loads(manifest.read_text())
        for record in frozen["outputs"]:
            path = ROOT / record["path"]
            if path == INPUT or path.parent in {SUPPLY, NETWORK}:
                with path.open("rb") as handle:
                    actual = hashlib.file_digest(handle, "sha256").hexdigest()
                assert actual == record["sha256"], str(path)
    for sub in ["multipliers", "simultaneous", "all_firms", "samples", "checkpoints"]:
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    months = [f"2025{m:02}" for m in range(4, 13)] + ["202601", "202602", "202603"]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        tasks = [executor.submit(month_work, m) for m in months]
        for future in as_completed(tasks):
            future.result()
    print("ALL MONTHS COMPLETE", flush=True)


if __name__ == "__main__":
    main()
