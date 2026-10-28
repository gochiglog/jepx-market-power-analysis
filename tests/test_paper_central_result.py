import pandas as pd
import pytest

from src.paper_central_result import CATEGORIES, COMMON, artificial_fence_example, three_categories


def sample():
    rows = []
    outcomes = [
        ("feasible", "feasible", "feasible", "feasible"),
        ("feasible", "feasible", "infeasible", "feasible"),
        ("feasible", "feasible", "infeasible", "infeasible"),
        ("infeasible", "feasible", "infeasible", "feasible"),
        ("held_network_input", "feasible", "held_network_input", "feasible"),
    ]
    for p, (bs, bf, rs, rf) in enumerate(outcomes, 1):
        for ns, b, r in [("saved_plan_constraints", bs, rs), ("no_transmission_bounds", bf, rf)]:
            rows.append(
                dict(
                    entity_code="jera",
                    delivery_date="2025-04-01",
                    period=p,
                    network_scenario=ns,
                    all_firms_status=b,
                    removed_simultaneous_status=r,
                )
            )
    return pd.DataFrame(rows)


def test_three_exclusive_groups_keep_infeasible_baseline_and_unknown_outside():
    result = three_categories(sample()).sort_values("period")
    assert result.central_category.iloc[:3].tolist() == CATEGORIES
    assert result.comparison_scope.iloc[:3].eq(COMMON).all()
    assert result.central_category.iloc[3:].isna().all()
    assert result.comparison_scope.iloc[3:].tolist() == [
        "all_firms_infeasible",
        "held_network_input",
    ]
    assert len(result) == 5


def test_saved_feasible_free_infeasible_is_rejected():
    data = sample()
    data.loc[
        (data.period == 1) & data.network_scenario.eq("no_transmission_bounds"),
        "removed_simultaneous_status",
    ] = "infeasible"
    with pytest.raises(ValueError, match="包含"):
        three_categories(data)


@pytest.mark.parametrize("mode", ["duplicate", "missing"])
def test_multiplication_and_silent_unmatched_time_are_rejected(mode):
    data = sample()
    data = pd.concat([data, data.iloc[:1]]) if mode == "duplicate" else data.iloc[1:]
    with pytest.raises(ValueError, match="重複|未一致"):
        three_categories(data)


def test_same_variables_must_satisfy_individual_and_fence_bounds():
    a = artificial_fence_example(80, -30)
    b = artificial_fence_example(80, 30)
    assert a["individual_ok"] and not a["fence_ok"] and a["F_H"] == 110
    assert b["individual_ok"] and b["fence_ok"] and b["F_H"] == 50
