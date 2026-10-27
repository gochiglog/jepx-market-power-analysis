import pandas as pd

from scripts.publish_annual_diagnostic import classify


def test_consistency_keeps_baseline_scope_and_unknown_outside_removed_failure():
    f = pd.DataFrame(
        {"all_firms_status": ["feasible"] * 17, "removed_simultaneous_status": ["infeasible"] * 17}
    )
    assert classify(f) == "all17_removed_infeasible"
    f.loc[0, "removed_simultaneous_status"] = "feasible"
    assert classify(f) == "condition_dependent"
    f.loc[0, "all_firms_status"] = "infeasible"
    assert classify(f) == "baseline_scope_differs"
    f.loc[0, "all_firms_status"] = "held_network_input"
    assert classify(f) == "held"
    assert classify(f.iloc[:16]) == "held"


def test_simultaneous_comparison_accepts_negative_net_independently_of_multiplier():
    f = pd.DataFrame(
        {
            "all_firms_status": ["feasible"] * 17,
            "removed_simultaneous_status": ["feasible"] * 17,
            "target_net_demand": [-5] * 17,
        }
    )
    assert classify(f) == "all17_removed_feasible"
