"""企業除去試行の原値保持と、変数を消去した別定式化による標本最適値検算。"""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.optimize import linprog


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def validate_solution_samples(root, folder):
    # srcの最大倍率関数を使わず、地域発電gを消去した11変数LPを組む。
    import sys

    sys.path.insert(0, str(root))
    from src.net_demand_network import AREAS, EDGES

    samples = pd.read_parquet(folder / "multiplier_solution_samples.parquet")
    inputs = pd.read_parquet(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    ).set_index(["area", "delivery_date", "period"])
    network = pd.read_parquet(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet"
    )
    groups = {(d, p): g for (d, p), g in network.groupby(["delivery_date", "period"])}
    a = np.zeros((9, 10))
    for i, (_, sender, receiver) in enumerate(EDGES):
        a[AREAS.index(sender), i] = -1
        a[AREAS.index(receiver), i] = 1
    max_error = 0.0
    for r in samples.itertuples():
        net = inputs.loc[[(x, r.delivery_date, r.period) for x in AREAS]].net_demand_B_mw.to_numpy(
            float
        )
        target = AREAS.index(r.target_area)
        base = net.copy()
        base[target] = 0
        u = np.array(json.loads(r.upper_mw_json))
        g = np.array(json.loads(r.generation_mw_json))
        f = np.array(json.loads(r.flows_mw_json))
        rhs = net.copy()
        rhs[target] *= r.multiplier
        assert np.abs(g + a @ f - rhs).max() < 1e-6
        assert (g >= -1e-6).all() and (g <= u + 1e-6).all()
        constraints = groups[r.delivery_date, r.period]
        c = np.array([json.loads(v) for v in constraints.coefficients_json])
        lo = constraints.lower_mw.to_numpy(float)
        hi = constraints.upper_mw.to_numpy(float)
        assert ((c @ f) >= lo - 1e-6).all() and ((c @ f) <= hi + 1e-6).all()
        m = np.column_stack([-a, np.eye(9)[:, target]])
        network_rows = np.column_stack([c, np.zeros(len(c))])
        answer = linprog(
            np.r_[np.zeros(10), -1.0],
            A_ub=np.vstack([m, -m, network_rows, -network_rows]),
            b_ub=np.r_[u - base, base, hi, -lo],
            bounds=[(None, None)] * 10 + [(0, None)],
            method="highs",
        )
        assert answer.success
        error = abs(answer.x[-1] / net[target] - r.multiplier)
        assert error < 1e-7
        max_error = max(max_error, error)
    return {
        "sample_optimum_checks": len(samples),
        "maximum_independent_multiplier_difference": max_error,
    }


def run(root, folder):
    qa = root / "reports/21_adjustable_removal/qa"
    audit = json.loads((qa / "run_audit.json").read_text())
    for record in audit["inputs"] + audit["outputs"]:
        assert sha(root / record["path"]) == record["sha256"], record["path"]
    original_dir = (
        root / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1/unit_scenarios"
    )
    checked = 0
    allowed = {
        "held_invalid_dates",
        "held_unknown_recovery",
        "held_update_candidates",
        "held_multiple_contents",
        "held_unknown_record_type",
        "held_missing_mean_reduction",
        "held_invalid_mean_reduction",
        "held_partial_start_cell",
    }
    for path in sorted((folder / "unit_trial_uppers").glob("*.parquet")):
        name = path.stem.removesuffix("__record_held_reference")
        source = pq.read_table(
            original_dir / (name + ".parquet"),
            filters=[
                ("delivery_date", ">=", date(2025, 4, 1)),
                ("delivery_date", "<=", date(2025, 4, 30)),
            ],
        )
        saved = pq.read_table(path)
        assert saved.select(source.column_names).equals(source)
        frame = saved.to_pandas()
        restored = frame.record_held_reference_restored
        assert frame.loc[restored, "eligible_adjustable_candidate"].all()
        assert frame.loc[restored, "scenario_status"].isin(allowed).all()
        assert (
            frame.loc[restored, "trial_adjustable_upper_kw"]
            .eq(frame.loc[restored, "capacity_reference_kw"])
            .all()
        )
        retained = frame.eligible_adjustable_candidate & ~restored
        old = frame.loc[retained, "adjusted_reference_upper_kw"].tolist()
        new = frame.loc[retained, "trial_adjustable_upper_kw"].tolist()
        assert old == new
        assert (
            frame.loc[~frame.eligible_adjustable_candidate, "trial_adjustable_upper_kw"]
            .isna()
            .all()
        )
        checked += len(frame)
    results = pd.read_parquet(folder / "all_and_removed_net_multipliers.parquet")
    assert len(results) == 440640
    valid = results.status.eq("valid")
    assert results.loc[valid, "lambda_removed"].notna().all()
    assert results.loc[~valid, "lambda_removed"].isna().all()
    assert results.removed_fixed_output_mw.eq(0).all()
    assert (
        results.loc[valid, "lambda_removed"] <= results.loc[valid, "lambda_all_firms"] + 1e-7
    ).all()
    paired = pd.read_parquet(folder / "network_comparison_common_times.parquet")
    common = paired[paired.common_valid]
    assert (common.lambda_removed_network <= common.lambda_removed_relaxed + 1e-7).all()
    proxy = pd.read_parquet(folder / "asset_attribution_proxy.parquet")
    assert len(proxy) == 491 and not proxy.asset_key_candidate.duplicated().any()
    assert not proxy.final_attribution_assigned.any()
    selected = proxy[proxy.eligible_adjustable_candidate]
    targets = [json.loads(v)[0] for v in selected.hjks_candidate_rows_json]
    assert len(targets) == len(set(targets)) == 372
    assert not selected.candidate_analysis_role.eq("fixed_observed_nuclear_candidate").any()
    # 制約なしの全有効値を、別読取の地域部分和から検算する。
    regional = pd.read_parquet(folder / "regional_trial_uppers.parquet")
    grouped = regional.groupby(["supply_trial_scenario", "delivery_date", "period"])
    supply = grouped[
        ["upper_limited_mw"]
        + [
            f"remove_{x}_mw"
            for x in [
                "hokkaido",
                "tohoku",
                "jera",
                "hokuriku",
                "kansai",
                "chugoku",
                "shikoku",
                "kyushu",
            ]
        ]
    ].sum()
    primary = pd.read_parquet(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    )
    net_sum = primary.groupby(["delivery_date", "period"]).net_demand_B_mw.sum().to_dict()
    relaxed = results[valid & results.network_scenario.eq("no_transmission_bounds")]
    max_relaxed_error = 0.0
    for r in relaxed.itertuples():
        row = supply.loc[r.supply_trial_scenario, r.delivery_date, r.period]
        remaining = float(row.upper_limited_mw - row["remove_" + r.entity_code + "_mw"])
        expected = (
            remaining - float(net_sum[r.delivery_date, r.period]) + r.target_net_demand_mw
        ) / r.target_net_demand_mw
        assert abs(expected - r.lambda_removed) < 1e-7
        max_relaxed_error = max(max_relaxed_error, abs(expected - r.lambda_removed))
    checks = validate_solution_samples(root, folder)
    checks.update(
        {
            "preserved_original_unit_rows": checked,
            "result_rows": len(results),
            "all_relaxed_valid_values_checked": len(relaxed),
            "maximum_relaxed_formula_difference": max_relaxed_error,
            "source_and_output_sha_verified": True,
            "fixed_R_removal_zero": True,
            "all_491_labels_retained": True,
            "reused_hjks_target_rows_in_adjustable_set": 0,
        }
    )
    (qa / "independent_validation.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n"
    )
    print(checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.output.resolve())
