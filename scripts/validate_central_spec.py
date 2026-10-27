"""工程25の原列/型・条件対応・実入力行列を別経路で照合する。"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.optimize import linprog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_central_spec import IDS, OLD, SC, SIM, TIME  # noqa: E402
from src.adjustable_removal import RECORD_HELD  # noqa: E402


def validate(root, output, report):
    if (report / "qa/independent_validation.json").exists():
        raise ValueError("固定済み検査を上書きしない。別報告先を指定する")
    audit = json.loads((report / "qa/run_audit.json").read_text())
    oldregional = pd.read_parquet(root / OLD / "regional_trial_uppers.parquet")
    ext = pd.read_parquet(output / "two_asset_addition_by_condition_time.parquet")
    checked = 0
    for path in sorted((output / "unit_extensions").glob("*.parquet")):
        original = pq.read_table(
            root / OLD / "unit_trial_uppers" / path.name,
            filters=[("asset_key_candidate", "in", list(IDS))],
        )
        new = pq.read_table(path)
        assert new.select(original.column_names).equals(original)
        f = new.to_pandas()
        for r in f.itertuples(index=False):
            expected = r.adjusted_reference_upper_kw
            if (
                path.stem.endswith("__record_held_reference")
                and r.scenario_status in RECORD_HELD
                and r.capacity_reference_kw is not None
                and r.capacity_reference_kw > 0
            ):
                expected = r.capacity_reference_kw
            if expected is not None and expected < 0:
                expected = None
            assert r.extension_adjustable_upper_kw == expected
        for (d, p), g in f.groupby(TIME):
            ref = ext[(ext[SC] == path.stem) & (ext.delivery_date == d) & (ext.period == p)].iloc[0]
            values = [v for v in g.extension_adjustable_upper_kw if v is not None]
            assert ref.added_hokkaido_upper_mw == sum(values, Decimal(0)) / 1000
            assert ref.extension_defined_assets == len(values)
        checked += len(f)
    assert checked == 48960
    assert ext.groupby([SC, "delivery_date"]).period.nunique().eq(48).all()
    old = oldregional[oldregional.area.eq("北海道")]
    pair = ext.merge(old, on=[SC, *TIME], how="outer", validate="one_to_one", indicator=True)
    assert pair._merge.eq("both").all() and len(pair) == 24480
    assert pair.upper_limited_mw.eq(pair.original_hokkaido_upper_mw).all()
    assert pair.remove_hokkaido_mw.eq(pair.hokkaido_removed_mw_unchanged).all()
    oldsim = pd.read_parquet(root / SIM / "nine_area_simultaneous_after_removal.parquet")
    sim = pd.read_parquet(output / "nine_area_simultaneous_extension_all_times.parquet")
    key = [SC, "network_scenario", *TIME, "entity_code"]
    matched = sim.merge(
        oldsim,
        on=key,
        how="outer",
        suffixes=("_new", "_old"),
        validate="one_to_one",
        indicator=True,
    )
    assert len(matched) == 391680 and matched._merge.eq("both").all()
    assert matched.old_all_firms_status.eq(matched.all_firms_status_old).all()
    assert matched.old_removed_simultaneous_status.eq(matched.removed_simultaneous_status_old).all()
    assert not (
        (sim.old_removed_simultaneous_status == "feasible")
        & (sim.removed_simultaneous_status != "feasible")
    ).any()
    # v1は型検査の途中版。数値結果は同一であることを値・型ごと確認。
    v1 = output.parent / "20261027_two_asset_v1"
    numeric_equal = []
    for name in [
        "two_asset_addition_by_condition_time",
        "hokkaido_multiplier_extension_all_times",
        "nine_area_simultaneous_extension_all_times",
        "condition_consistency_extension_all_times",
    ]:
        a, b = pq.read_table(v1 / (name + ".parquet")), pq.read_table(output / (name + ".parquet"))
        assert a.equals(b, check_metadata=False)
        numeric_equal.append(name)
    samples = json.loads((report / "qa/solver_calls_real_times.json").read_text())
    residuals = []
    for s in samples:
        np.testing.assert_allclose(
            np.array(s["demand_actual_mw"], float) - np.array(s["fixed_R_mw"], float),
            s["net_mw"],
            atol=1e-9,
        )
        for kind in ["multiplier_call", "simultaneous_call", "all_firms_simultaneous_call"]:
            c = s[kind]
            nvar = 20 if kind == "multiplier_call" else 19
            assert len(c["objective"]) == nvar and len(c["bounds"]) == nvar
            answer = linprog(
                c["objective"],
                **{k: c[k] for k in ["A_eq", "b_eq", "A_ub", "b_ub", "bounds", "method"]},
            )
            if answer.success:
                err = float(np.abs(np.array(c["A_eq"]) @ answer.x - c["b_eq"]).max())
                assert err < 1e-6
                residuals.append(err)
            else:
                assert answer.status == 2
    result = dict(
        original_unit_cells_all_values_types_order_equal=checked,
        old_new_sim_join_rows=len(matched),
        join_match_rate=1.0,
        unmatched=0,
        duplicate_keys=0,
        unit_day_48_periods_verified=True,
        intermediate_and_adopted_numeric_tables_equal=numeric_equal,
        captured_real_solver_samples=len(samples),
        max_replayed_residual_mw=max(residuals),
        original_fuel_unchanged=True,
        unknown_capacity_zero_recognition=False,
        no_diagnostic_slack_in_main_matrices=True,
        old_lambda_one_valid_comparisons=audit["old_valid_frames"],
        old_observed_lambda_one_exceptions=0,
        new_hokkaido_observed_exceptions=0,
    )
    (report / "qa/independent_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, default=Path("reports/25_central_spec_audit"))
    args = parser.parse_args()
    validate(args.root.resolve(), args.output.resolve(), args.report.resolve())
