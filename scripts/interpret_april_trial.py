"""工程21固定版の図・比較と、8法人の9地域同時成立検査を新規保存する。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.trial_adjustable_removal import ENTITIES  # noqa: E402
from src.april_trial_comparison import (  # noqa: E402
    CATEGORIES,
    display_categories,
    paired_stop_comparison,
    simultaneous_feasibility,
)
from src.net_demand_network import AREAS, EDGES  # noqa: E402

SOURCE = Path("data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3")
KEY = ["supply_trial_scenario", "delivery_date", "period", "target_area", "network_scenario"]
SIMKEY = ["supply_trial_scenario", "delivery_date", "period", "entity_code", "network_scenario"]


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(root, output, report):
    if output.exists() or (report / "qa/run_audit.json").exists():
        raise ValueError("既存の計算版/報告版を上書きしない")
    output.mkdir(parents=True)
    qa = report / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    inputs = {}
    outputs = []

    def read(path, **kwargs):
        inputs[str(path.relative_to(root))] = sha(path)
        return pq.read_table(path, **kwargs).to_pandas()

    def write(frame, name):
        path = output / (name + ".parquet")
        frame = frame.copy()
        for col in frame.select_dtypes(include=["category"]):
            frame[col] = frame[col].astype(str)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, path, compression="zstd")
        if not pq.read_table(path).equals(table):
            raise ValueError("Parquet再読込不一致")
        outputs.append(
            {"path": str(path.relative_to(root)), "sha256": sha(path), "rows": len(frame)}
        )

    r = read(root / SOURCE / "all_and_removed_net_multipliers.parquet")
    if len(r) != 440640 or r.duplicated(KEY).any():
        raise ValueError("固定版キー不一致")
    r["display_category"] = display_categories(r)
    write(r, "all_times_with_display_categories")
    flags = r[
        KEY
        + [
            "entity",
            "status",
            "display_category",
            "all_firms_balance_status",
            "target_net_demand_mw",
            "removed_multiplier_status",
        ]
    ].copy()
    flags["flag_all_firms_infeasible"] = flags.all_firms_balance_status.eq("infeasible")
    flags["flag_nonpositive_target_net"] = flags.target_net_demand_mw.le(0)
    flags["flag_removed_other_regions_infeasible"] = flags.removed_multiplier_status.eq(
        "other_regions_infeasible"
    )
    write(flags, "overlapping_reason_flags")
    flags.groupby(
        [
            "supply_trial_scenario",
            "network_scenario",
            "target_area",
            "status",
            "flag_all_firms_infeasible",
            "flag_nonpositive_target_net",
            "flag_removed_other_regions_infeasible",
        ]
    ).size().rename("count").reset_index().to_csv(qa / "overlapping_reason_counts.csv", index=False)
    main = r[
        r.supply_trial_scenario.eq("reference_no_deduction")
        & r.network_scenario.eq("saved_plan_constraints")
    ]
    state_rows = []
    for area in AREAS:
        g = main[main.target_area.eq(area)]
        assert len(g) == 1440
        nvalid = int(g.status.eq("valid").sum())
        for state in CATEGORIES:
            n = int(g.display_category.eq(state).sum())
            state_rows.append(
                {
                    "target_area": area,
                    "entity": g.entity.iloc[0],
                    "category": state,
                    "count": n,
                    "denominator_all": len(g),
                    "share_all": n / len(g),
                    "valid_time_count": nvalid,
                    "below_one_share_among_valid": None
                    if not nvalid
                    else float(g.display_category.eq("below_one").sum() / nvalid),
                }
            )
    pd.DataFrame(state_rows).to_csv(qa / "all_time_state_shares.csv", index=False)
    # 既存の条件別ネットワーク1対1表を再利用。片方保留も残っている。
    network_pair = read(root / SOURCE / "network_comparison_common_times.parquet")
    assert len(network_pair) == 220320
    ref_pair = network_pair[network_pair.supply_trial_scenario.eq("reference_no_deduction")].copy()
    ref_pair["category_network"] = display_categories(
        ref_pair.rename(
            columns={"status_network": "status", "lambda_removed_network": "lambda_removed"}
        )
    )
    ref_pair["category_relaxed"] = display_categories(
        ref_pair.rename(
            columns={"status_relaxed": "status", "lambda_removed_relaxed": "lambda_removed"}
        )
    )
    write(ref_pair, "reference_network_pairs_all_times")
    transitions = (
        ref_pair.groupby(
            [
                "target_area",
                "entity",
                "status_network",
                "status_relaxed",
                "category_network",
                "category_relaxed",
            ]
        )
        .size()
        .rename("count")
        .reset_index()
    )
    transitions.to_csv(qa / "network_status_transitions.csv", index=False)
    side = []
    for area in AREAS:
        g = ref_pair[ref_pair.target_area.eq(area)]
        common = g.status_network.eq("valid") & g.status_relaxed.eq("valid")
        side.append(
            {
                "target_area": area,
                "entity": g.entity.iloc[0],
                "all_times": len(g),
                "common_valid": int(common.sum()),
                "network_only_valid": int(
                    (g.status_network.eq("valid") & ~g.status_relaxed.eq("valid")).sum()
                ),
                "relaxed_only_valid": int(
                    (~g.status_network.eq("valid") & g.status_relaxed.eq("valid")).sum()
                ),
                "neither_valid": int(
                    (~g.status_network.eq("valid") & ~g.status_relaxed.eq("valid")).sum()
                ),
                "below_one_network_common": int(
                    g.loc[common, "category_network"].eq("below_one").sum()
                ),
                "below_one_relaxed_common": int(
                    g.loc[common, "category_relaxed"].eq("below_one").sum()
                ),
                "median_network_common": g.loc[common, "lambda_removed_network"].median(),
                "median_relaxed_common": g.loc[common, "lambda_removed_relaxed"].median(),
            }
        )
    pd.DataFrame(side).to_csv(qa / "network_validity_summary.csv", index=False)
    regional = read(root / SOURCE / "regional_trial_uppers.parquet")
    # 既知部分和の列だけを集計する。未知の部分和をsumで0へ変えない。
    numeric_columns = ["upper_limited_mw", "restored_reference_mw"] + [
        "remove_" + e + "_mw" for e in ENTITIES
    ]
    if regional[numeric_columns].isna().any().any():
        raise ValueError("地域既知部分和にnull。0へ補わず入力を保留する")
    if (
        not regional.groupby(["supply_trial_scenario", "delivery_date", "period"])
        .size()
        .eq(9)
        .all()
    ):
        raise ValueError("9地域の既知部分和が揃わない")
    aggregate = regional.groupby(["supply_trial_scenario", "delivery_date", "period"])[
        ["upper_limited_mw", "restored_reference_mw"] + ["remove_" + e + "_mw" for e in ENTITIES]
    ].sum()
    stop_frames = []
    restored_rows = []
    stop_summary = []
    original_names = sorted(
        x
        for x in r.supply_trial_scenario.unique()
        if x != "reference_no_deduction" and not x.endswith("__record_held_reference")
    )
    for name in original_names:
        before = r[r.supply_trial_scenario.eq(name)]
        after = r[r.supply_trial_scenario.eq(name + "__record_held_reference")]
        pair = paired_stop_comparison(before, after)
        pair["original_scenario"] = name
        for day, period in (
            before[["delivery_date", "period"]].drop_duplicates().itertuples(index=False)
        ):
            b = aggregate.loc[name, day, period]
            a = aggregate.loc[name + "__record_held_reference", day, period]
            total = a.upper_limited_mw - b.upper_limited_mw
            assert total == a.restored_reference_mw
            for code, entity in ENTITIES.items():
                own = a["remove_" + code + "_mw"] - b["remove_" + code + "_mw"]
                other = total - own
                assert own >= 0 and other >= 0
                assert (
                    (a.upper_limited_mw - a["remove_" + code + "_mw"])
                    - (b.upper_limited_mw - b["remove_" + code + "_mw"])
                ) == other
                restored_rows.append(
                    {
                        "original_scenario": name,
                        "delivery_date": day,
                        "period": int(period),
                        "entity_code": code,
                        "entity": entity,
                        "restored_total_reference_mw": total,
                        "restored_target_reference_mw": own,
                        "restored_other_reference_mw": other,
                    }
                )
        stop_frames.append(pair)
        for (area, network), g in pair.groupby(["target_area", "network_scenario"]):
            common = g[g.both_valid]
            stop_summary.append(
                {
                    "original_scenario": name,
                    "target_area": area,
                    "network_scenario": network,
                    "all_times": len(g),
                    "valid_before": int(g.status_before.eq("valid").sum()),
                    "valid_after": int(g.status_after.eq("valid").sum()),
                    "both_valid": len(common),
                    "lambda_difference_min": common.lambda_difference.min(),
                    "lambda_difference_median": common.lambda_difference.median(),
                    "lambda_difference_max": common.lambda_difference.max(),
                    "below_to_above": int(g.below_to_above.sum()),
                    "above_to_below": int(g.above_to_below.sum()),
                    "became_valid_from_all_firms_infeasible": int(
                        (g.became_valid & g.status_before.eq("all_firms_infeasible")).sum()
                    ),
                    "became_valid_from_other_regions_infeasible": int(
                        (
                            g.became_valid & g.status_before.eq("removed_other_regions_infeasible")
                        ).sum()
                    ),
                    "became_valid_from_other_held": int(
                        (
                            g.became_valid
                            & ~g.status_before.isin(
                                ["all_firms_infeasible", "removed_other_regions_infeasible"]
                            )
                        ).sum()
                    ),
                }
            )
    restoration = pd.DataFrame(restored_rows)
    assert len(restoration) == 8 * 1440 * 8
    write(restoration, "restored_reference_by_entity_time")
    stop = pd.concat(stop_frames, ignore_index=True)
    # 同じJERAの復帰量は東京/中部の説明へ反復する。国合計は上の8法人表から参照。
    stop = stop.merge(
        restoration.drop(columns="entity"),
        left_on=["original_scenario", "delivery_date", "period", "entity_code_before"],
        right_on=["original_scenario", "delivery_date", "period", "entity_code"],
        how="left",
        validate="many_to_one",
        indicator="restoration_join",
    )
    assert stop.restoration_join.eq("both").all() and len(stop) == 207360
    write(stop, "paired_stop_cases_all_times")
    pd.DataFrame(stop_summary).to_csv(qa / "stop_pair_summary.csv", index=False)
    stop.groupby(
        [
            "original_scenario",
            "target_area",
            "network_scenario",
            "status_before",
            "status_after",
            "display_category_before",
            "display_category_after",
        ]
    ).size().rename("count").reset_index().to_csv(qa / "stop_status_transitions.csv", index=False)
    quantities = []
    for (name, code), g in restoration.groupby(["original_scenario", "entity_code"]):
        quantities.append(
            {
                "original_scenario": name,
                "entity_code": code,
                "time_keys": len(g),
                **{
                    col + "_" + stat: float(getattr(g[col], stat)())
                    for col in [
                        "restored_total_reference_mw",
                        "restored_target_reference_mw",
                        "restored_other_reference_mw",
                    ]
                    for stat in ["min", "mean", "max"]
                },
            }
        )
    pd.DataFrame(quantities).to_csv(qa / "restored_reference_summary.csv", index=False)
    # 新規検査：8法人、lambdaなし、全9地域の元Nを同時に満たすか。
    primary = read(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    ).set_index(["area", "delivery_date", "period"])
    from datetime import date

    network = read(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet",
        filters=[
            ("delivery_date", ">=", date(2025, 4, 1)),
            ("delivery_date", "<=", date(2025, 4, 30)),
        ],
    )
    assert network.groupby("constraint_name").coefficients_json.nunique().eq(1).all()
    networks = {(d, p): g for (d, p), g in network.groupby(["delivery_date", "period"])}
    incidence = np.zeros((9, 10))
    for i, (_, sender, receiver) in enumerate(EDGES):
        incidence[AREAS.index(sender), i] = -1
        incidence[AREAS.index(receiver), i] = 1
    cache = {}
    sim_rows = []
    samples = []
    sample_keys = set()
    base_check = []
    for name, frame in regional.groupby("supply_trial_scenario", sort=True):
        print("simultaneous", name, flush=True)
        for (day, period), g in frame.groupby(["delivery_date", "period"], sort=True):
            g = g.set_index("area").loc[list(AREAS)]
            net = primary.loc[[(a, day, period) for a in AREAS]].net_demand_B_mw.to_numpy(float)
            u = g.upper_limited_mw.to_numpy(float)
            ng = networks[day, period]
            c = np.array([json.loads(x) for x in ng.coefficients_json], float)
            lo = ng.lower_mw.to_numpy(float)
            hi = ng.upper_mw.to_numpy(float)
            for constrained in [True, False]:
                ns = "saved_plan_constraints" if constrained else "no_transmission_bounds"
                bounds = tuple(np.r_[lo, hi]) if constrained else ()

                def check(up, tag):
                    key = (tuple(net), tuple(up), bounds)
                    if key not in cache:
                        result = simultaneous_feasibility(
                            net, up, incidence, c, lo, hi, constrained=constrained
                        )
                        # 少数の成立割当だけ保存。全解で関数内の残差検査を実行。
                        sk = (name, tag, ns)
                        if (
                            constrained
                            and result["status"] == "feasible"
                            and len(samples) < 200
                            and sk not in sample_keys
                        ):
                            samples.append(
                                {
                                    "supply_trial_scenario": name,
                                    "delivery_date": day,
                                    "period": int(period),
                                    "upper_mw_json": json.dumps(up.tolist()),
                                    "generation_mw_json": json.dumps(result["generation_mw"]),
                                    "flows_mw_json": json.dumps(result["flows_mw"]),
                                }
                            )
                            sample_keys.add(sk)
                        cache[key] = {k: v for k, v in result.items() if not isinstance(v, list)}
                    return cache[key]

                full = check(u, "all_firms")
                base_check.append(
                    {
                        "supply_trial_scenario": name,
                        "delivery_date": day,
                        "period": int(period),
                        "network_scenario": ns,
                        "sim_all_firms_status": full["status"],
                    }
                )
                for code, entity in ENTITIES.items():
                    remaining = (g.upper_limited_mw - g["remove_" + code + "_mw"]).to_numpy(float)
                    removed = check(remaining, code)
                    if full["status"] == "infeasible" and removed["status"] == "feasible":
                        raise ValueError("上限除去で成立が改善")
                    if full["status"] == "infeasible":
                        transition = "all_firms_already_infeasible"
                    elif full["status"] == "feasible" and removed["status"] == "feasible":
                        transition = "all_feasible_removed_feasible"
                    elif full["status"] == "feasible" and removed["status"] == "infeasible":
                        transition = "all_feasible_removed_infeasible"
                    else:
                        transition = "held_or_numerical_failure"
                    sim_rows.append(
                        {
                            "supply_trial_scenario": name,
                            "delivery_date": day,
                            "period": int(period),
                            "entity_code": code,
                            "entity": entity,
                            "network_scenario": ns,
                            "all_firms_status": full["status"],
                            "removed_simultaneous_status": removed["status"],
                            "transition": transition,
                            "removed_adjustable_mw_all_nine_areas": float(
                                g["remove_" + code + "_mw"].sum()
                            ),
                            "fixed_R_removed": False,
                            "removed_max_residual_mw": removed.get("residual_mw"),
                        }
                    )
    sim = pd.DataFrame(sim_rows)
    assert len(sim) == 17 * 1440 * 8 * 2 and not sim.duplicated(SIMKEY).any()
    write(sim, "nine_area_simultaneous_after_removal")
    write(pd.DataFrame(samples), "simultaneous_solution_samples")
    sim.groupby(
        ["supply_trial_scenario", "network_scenario", "entity_code", "entity", "transition"]
    ).size().rename("time_keys").reset_index().to_csv(qa / "simultaneous_summary.csv", index=False)
    write(pd.DataFrame(base_check), "all_firms_simultaneous_baseline")
    linked = r.merge(
        sim, on=SIMKEY, how="left", validate="many_to_one", indicator=True, suffixes=("", "_sim")
    )
    assert linked._merge.eq("both").all() and len(linked) == 440640
    assert linked.all_firms_balance_status.eq(linked.all_firms_status).all()
    linked["valid_below_one_and_sim_feasible"] = linked.display_category.eq(
        "below_one"
    ) & linked.removed_simultaneous_status.eq("feasible")
    linked["other_regions_infeasible_and_sim_feasible"] = linked.status.eq(
        "removed_other_regions_infeasible"
    ) & linked.removed_simultaneous_status.eq("feasible")
    linked["valid_above_one_but_sim_infeasible"] = linked.display_category.eq(
        "at_least_one"
    ) & linked.removed_simultaneous_status.eq("infeasible")
    assert not linked.valid_below_one_and_sim_feasible.any()
    assert not linked.other_regions_infeasible_and_sim_feasible.any()
    write(linked, "multiplier_simultaneous_relationship")
    linked.groupby(
        [
            "supply_trial_scenario",
            "network_scenario",
            "target_area",
            "status",
            "display_category",
            "removed_simultaneous_status",
        ]
    ).size().rename("count").reset_index().to_csv(
        qa / "multiplier_simultaneous_relationship.csv", index=False
    )
    checks = {
        "source_frames": 440640,
        "display_frames": len(r),
        "network_pair_frames": len(network_pair),
        "stop_pair_frames": len(stop),
        "restoration_entity_time_frames": len(restoration),
        "simultaneous_company_frames": len(sim),
        "relationship_join_matches": len(linked),
        "relationship_unmatched": 0,
        "network_time_join_rate": 1.0,
        "restoration_join_rate": 1.0,
        "baseline_match_rate": 1.0,
        "valid_above_one_but_sim_infeasible": int(linked.valid_above_one_but_sim_infeasible.sum()),
        "simultaneous_numeric_or_input_held": int(
            sim.transition.eq("held_or_numerical_failure").sum()
        ),
        "real_lp_unique_evaluations": sum(len(k[2]) > 0 for k in cache),
        "cached_or_analytic_evaluations_not_independent_observations": True,
        "unit": "MW; multiplier ratio; time count 30min",
        "raw_or_fixed_inputs_modified": False,
    }
    for p, h in inputs.items():
        if sha(root / p) != h:
            raise ValueError("入力SHA変更")
    audit = {
        "version": output.name,
        "inputs": [{"path": p, "sha256": h} for p, h in inputs.items()],
        "outputs": outputs,
        "qa": checks,
        "report_directory": str(report.relative_to(root)),
    }
    (qa / "run_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    (output / "manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(checks, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    run(
        Path(__file__).resolve().parents[1],
        args.output.resolve(),
        args.report.resolve() if args.report else args.output.resolve() / "review",
    )
