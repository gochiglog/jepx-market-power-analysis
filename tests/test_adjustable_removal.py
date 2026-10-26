from decimal import Decimal

import numpy as np
import pytest

from src.adjustable_removal import maximum_multiplier, trial_upper

INCIDENCE = np.array([[-1.0], [1.0]])


def solve(net, upper, target=1, limit=1000, constrained=True):
    return maximum_multiplier(
        net, upper, target, INCIDENCE, [[1]], [-limit], [limit], constrained=constrained
    )


def test_other_region_demand_is_kept_and_multiplier_is_not_capped_at_one():
    answer = solve([50, 100], [200, 100])
    assert answer["status"] == "valid"
    assert answer["multiplier"] == pytest.approx(2.5)


def test_same_firm_is_removed_at_both_locations_and_fixed_output_stays():
    # D=[100,200], R=[50,100]を両条件で残す。実績Gを再控除しない。
    net = np.array([50, 100])
    full = np.array([150, 100])
    removal = np.array([60, 40])
    before = solve(net, full)
    after = solve(net, full - removal)
    assert before["multiplier"] == pytest.approx(2)
    assert after["multiplier"] == pytest.approx(1)
    np.testing.assert_array_equal(net, [50, 100])


def test_positive_multiplier_can_absorb_fixed_output_even_when_lambda_zero_cannot():
    answer = solve([-30, 10], [0, 0], limit=30)
    assert answer["status"] == "valid"
    assert answer["multiplier"] == pytest.approx(3)


def test_other_region_infeasible_is_null_not_zero():
    answer = solve([50, 100], [10, 0])
    assert answer["status"] == "other_regions_infeasible"
    assert answer["multiplier"] is None


def test_valid_zero_is_distinct_from_infeasible():
    answer = solve([50, 100], [50, 0])
    assert answer["status"] == "valid"
    assert answer["multiplier"] == pytest.approx(0)


def test_nonpositive_denominator_and_unknown_have_distinct_statuses():
    assert solve([10, 0], [20, 20])["status"] == "nonpositive_target_net"
    assert solve([10, -1], [20, 20])["multiplier"] is None
    assert solve([10, np.nan], [20, 20])["status"] == "missing_input"
    assert solve([10, 10], [-1, 20])["status"] == "negative_upper_held"


def test_network_bound_and_relaxed_same_inputs():
    net, upper = [20, 100], [200, 0]
    assert solve(net, upper, limit=50)["multiplier"] == pytest.approx(0.5)
    assert solve(net, upper, limit=50, constrained=False)["multiplier"] == pytest.approx(1.8)


def test_relaxed_closed_form_matches_large_bound_transport_model():
    for net, upper in [([30, 100], [100, 200]), ([-30, 10], [0, 0]), ([50, 100], [10, 0])]:
        analytic = solve(net, upper, constrained=False)
        lp = solve(net, upper)
        assert analytic["status"] == lp["status"]
        if analytic["status"] == "valid":
            assert analytic["multiplier"] == pytest.approx(lp["multiplier"])


def test_record_fallback_preserves_original_and_never_fills_capacity_unknown():
    original = None
    assert trial_upper(
        original, Decimal("100"), "held_unknown_recovery", restore_record_held=True
    ) == (Decimal("100"), True)
    assert original is None
    assert trial_upper(None, None, "held_unknown_recovery", restore_record_held=True) == (
        None,
        False,
    )
    assert trial_upper(
        None, Decimal("100"), "held_reference_capacity", restore_record_held=True
    ) == (None, False)
    assert trial_upper(
        None, Decimal("100"), "held_mapping_or_period", restore_record_held=True
    ) == (None, False)
    assert trial_upper(
        Decimal("-5"), Decimal("100"), "negative_adjusted_upper_retained", restore_record_held=True
    ) == (Decimal("-5"), False)


def test_valid_stop_zero_and_record_absent_are_not_overwritten_by_fallback():
    assert trial_upper(
        Decimal(0), Decimal(100), "stop_upper_zero_under_scenario", restore_record_held=True
    ) == (Decimal(0), False)
    assert trial_upper(
        None, Decimal(100), "held_missing_mean_reduction", restore_record_held=False
    ) == (None, False)


def test_real_trial_samples_and_fixed_output_contract():
    import json
    from pathlib import Path

    import pandas as pd

    from scripts.verify_adjustable_removal import validate_solution_samples

    root = Path(__file__).resolve().parents[1]
    folder = root / "data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3"
    if not (folder / "all_and_removed_net_multipliers.parquet").exists():
        pytest.skip("ローカルの完成した診断版を使うintegration test")
    checks = validate_solution_samples(root, folder)
    assert checks["sample_optimum_checks"] > 0
    results = pd.read_parquet(folder / "all_and_removed_net_multipliers.parquet")
    assert len(results) == 440640
    assert not results.duplicated(
        ["supply_trial_scenario", "delivery_date", "period", "target_area", "network_scenario"]
    ).any()
    assert results.removed_fixed_output_mw.eq(0).all()
    assert results.loc[~results.status.eq("valid"), "lambda_removed"].isna().all()
    assert not results.status.isin(
        ["missing_input", "numerical_failure", "negative_upper_held"]
    ).any()
    # 元の9条件の全法人成立/不成立は工程20と一致する。
    before = pd.read_parquet(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "all_firm_balance_results.parquet"
    )
    after = results[
        results.network_scenario.eq("saved_plan_constraints")
        & ~results.supply_trial_scenario.str.endswith("__record_held_reference")
    ]
    check = after.merge(
        before[["supply_scenario", "delivery_date", "period", "status"]],
        left_on=["supply_trial_scenario", "delivery_date", "period"],
        right_on=["supply_scenario", "delivery_date", "period"],
        how="left",
        validate="many_to_one",
        suffixes=("", "_old"),
        indicator=True,
    )
    assert check._merge.eq("both").all()
    assert check.all_firms_balance_status.eq(check.status_old).all()
    registry = pd.read_parquet(folder / "asset_attribution_proxy.parquet")
    jera = registry[registry.removal_entity_code.eq("jera")]
    assert jera.groupby("area").size().to_dict() == {"中部": 35, "東京": 48}
    assert not jera.candidate_analysis_role.eq("fixed_observed_nuclear_candidate").any()
    assert not registry.final_attribution_assigned.any()
    institutional = pd.read_csv(root / "metadata/research/regulatory_targets.csv")
    assert len(institutional) == 11 and institutional.regulatory_entity.nunique() == 10
    for original in root.glob("reports/21_adjustable_removal/qa/run_audit.json"):
        assert json.loads(original.read_text())["fixed_output_removed"] is False
