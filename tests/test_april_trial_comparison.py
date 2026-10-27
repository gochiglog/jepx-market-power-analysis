from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.april_trial_comparison import (
    BAND,
    CATEGORIES,
    display_categories,
    ecdf_points,
    paired_stop_comparison,
    simultaneous_feasibility,
)


def test_states_partition_preserves_primary_status_and_boundary():
    frame = pd.DataFrame(
        {
            "status": ["valid"] * 5
            + [
                "nonpositive_target_net",
                "all_firms_infeasible",
                "removed_other_regions_infeasible",
                "numerical_failure",
            ],
            "lambda_removed": [0.5, 1 - BAND, 1, 1 + BAND, 1.2, None, None, None, None],
            "all_firms_balance_status": ["feasible"] * 5 + ["infeasible"] * 4,
        }
    )
    actual = display_categories(frame)
    assert actual.tolist() == [
        "below_one",
        "numerical_boundary",
        "numerical_boundary",
        "numerical_boundary",
        "at_least_one",
        "nonpositive_target_net",
        "all_firms_infeasible",
        "removed_other_regions_infeasible",
        "other_held",
    ]
    assert len(actual) == len(frame) and set(actual) <= set(CATEGORIES)
    assert frame.status.iloc[5] == "nonpositive_target_net"


def test_invalid_valid_number_is_not_silently_dropped():
    with pytest.raises(ValueError):
        display_categories(pd.DataFrame({"status": ["valid"], "lambda_removed": [None]}))


def test_ecdf_ties_and_extreme_value_all_remain():
    x, y = ecdf_points([0.5, 0.5, 10000])
    np.testing.assert_array_equal(x, [0.5, 0.5, 10000])
    np.testing.assert_allclose(y, [0, 2 / 3, 1])
    with pytest.raises(ValueError):
        ecdf_points([1, None])


def pair_frame(status, values):
    f = pd.DataFrame(
        {
            "delivery_date": ["2025-04-01"] * len(status),
            "period": range(1, len(status) + 1),
            "target_area": "北海道",
            "entity": "北海道電力株式会社",
            "network_scenario": "saved_plan_constraints",
            "status": status,
            "lambda_removed": values,
        }
    )
    f["display_category"] = display_categories(f)
    return f


def test_stop_pair_keeps_held_rows_and_separates_valid_changes():
    before = pair_frame(
        ["valid", "removed_other_regions_infeasible", "all_firms_infeasible"], [0.7, None, None]
    )
    after = pair_frame(["valid", "valid", "all_firms_infeasible"], [1.1, 0.8, None])
    result = paired_stop_comparison(before, after)
    assert len(result) == 3
    assert result.below_to_above.tolist() == [True, False, False]
    assert result.became_valid.tolist() == [False, True, False]
    assert result.lambda_difference.iloc[0] == pytest.approx(0.4)
    assert result.lambda_difference.iloc[1:].isna().all()
    with pytest.raises(ValueError):
        paired_stop_comparison(before, after.iloc[:2])


def sim(net, upper, bound=1000, constrained=True):
    return simultaneous_feasibility(
        net, upper, np.array([[-1.0], [1.0]]), [[1]], [-bound], [bound], constrained=constrained
    )


def test_simultaneous_original_demands_fail_after_same_firm_removed_everywhere():
    assert sim([50, 100], [150, 100])["status"] == "feasible"
    assert sim([50, 100], [90, 60])["status"] == "feasible"
    assert sim([50, 100], [80, 50])["status"] == "infeasible"
    assert sim([50, 100], [200, 0], bound=50)["status"] == "infeasible"
    assert sim([50, 100], [200, 0], bound=50, constrained=False)["status"] == "feasible"


def test_simultaneous_nonpositive_local_net_is_not_ratio_held_or_clipped():
    assert sim([-30, 50], [0, 30])["status"] == "feasible"
    assert sim([-30, 10], [0, 0])["status"] == "infeasible"
    assert sim([10, None], [20, 20])["status"] == "input_held"
    assert sim([10, 10], [-1, 20])["status"] == "input_held"


def test_actual_comparison_denominators_and_entity_unit():
    root = Path(__file__).resolve().parents[1]
    out = root / "data/interim/occto/april_trial_comparison/20261027_comparison_v1"
    if not (out / "multiplier_simultaneous_relationship.parquet").exists():
        pytest.skip("ローカルの固定データを使用するintegration test")
    r = pd.read_parquet(out / "all_times_with_display_categories.parquet")
    assert len(r) == 440640
    reference = r[
        r.supply_trial_scenario.eq("reference_no_deduction")
        & r.network_scenario.eq("saved_plan_constraints")
    ]
    assert reference.groupby("target_area").size().eq(1440).all()
    assert len(reference) == 12960
    pairs = pd.read_parquet(out / "reference_network_pairs_all_times.parquet")
    counts = pairs.groupby("target_area").common_valid.sum().to_dict()
    assert counts["北海道"] == 1430 and counts["東京"] == 225 and counts["中部"] == 423
    assert counts["東京"] != 52
    sim_data = pd.read_parquet(out / "nine_area_simultaneous_after_removal.parquet")
    assert len(sim_data) == 391680 and sim_data.entity_code.nunique() == 8
    assert len(sim_data[sim_data.entity_code.eq("jera")]) == 17 * 1440 * 2
    linked = pd.read_parquet(out / "multiplier_simultaneous_relationship.parquet")
    assert len(linked) == 440640 and not linked.valid_below_one_and_sim_feasible.any()
    assert not linked.other_regions_infeasible_and_sim_feasible.any()
    assert not sim_data.fixed_R_removed.any()
    restored = pd.read_parquet(out / "restored_reference_by_entity_time.parquet")
    assert restored.restored_total_reference_mw.eq(
        restored.restored_target_reference_mw + restored.restored_other_reference_mw
    ).all()
