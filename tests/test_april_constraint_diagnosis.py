"""17条件の比較可能性、上下包絡解除、診断専用過不足の検査。"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.april_constraint_diagnosis import (
    condition_consistency,
    diagnostic_slack,
    pair_simultaneous_conditions,
    select_constraints,
)
from src.april_trial_comparison import simultaneous_feasibility


def conditions(base="feasible", removed="infeasible"):
    return pd.DataFrame(
        {
            "supply_trial_scenario": [f"c{i}" for i in range(17)],
            "all_firms_status": base,
            "removed_simultaneous_status": removed,
        }
    )


def test_17_conditions_do_not_confuse_baseline_failure_with_removal():
    g = conditions()
    assert condition_consistency(g)["consistency_state"] == "all17_base_feasible_removed_infeasible"
    g.loc[0, "all_firms_status"] = "infeasible"
    g.loc[1, "removed_simultaneous_status"] = "feasible"
    r = condition_consistency(g)
    assert r["consistency_state"] == "baseline_scope_differs"
    assert r["removed_varies_among_comparable"]
    assert r["baseline_feasible_conditions"] == 16
    assert r["removed_infeasible_comparable_conditions"] == 15
    with pytest.raises(ValueError):
        condition_consistency(g.iloc[:16])
    g.loc[1, "removed_simultaneous_status"] = "numerical_failure"
    assert condition_consistency(g)["consistency_state"] == "other_held"


def test_all_feasible_and_varying_comparable_states():
    g = conditions(removed="feasible")
    assert condition_consistency(g)["consistency_state"] == "all17_base_feasible_removed_feasible"
    g.loc[1, "removed_simultaneous_status"] = "infeasible"
    assert condition_consistency(g)["consistency_state"] == "removed_varies_among_baseline_feasible"


def test_overlapping_constraints_need_not_change_when_one_is_released():
    inc = np.array([[-1.0], [1.0]])
    c = np.array([[1.0], [1.0]])
    lo = np.array([-30.0, -30.0])
    hi = np.array([30.0, 30.0])
    names = ["individual", "fence"]
    for released in [[], ["individual"], ["fence"]]:
        cc, ll, hh = select_constraints(names, c, lo, hi, released)
        assert (
            simultaneous_feasibility([0, 50], [100, 0], inc, cc, ll, hh)["status"] == "infeasible"
        )
    cc, ll, hh = select_constraints(names, c, lo, hi, ["individual", "fence"])
    assert cc.shape == (0, 1) and len(ll) == 0 and len(hh) == 0
    with pytest.raises(ValueError):
        select_constraints(names, c, lo, hi, ["unknown"])
    # 上下両方向を解除し、負方向も残る制約に従う。
    cc, ll, hh = select_constraints(names, c, lo, hi, ["individual"])
    assert ll.tolist() == [-30.0] and hh.tolist() == [30.0]


def test_diagnostic_deficit_and_local_absorption_do_not_modify_inputs():
    inc = np.array([[-1.0], [1.0]])
    net = np.array([-30.0, 50.0])
    upper = np.array([0.0, 30.0])
    d = diagnostic_slack(net, upper, inc, [[1]], [-10], [10])
    assert sum(d["diagnostic_surplus_mw"]) == pytest.approx(20)
    assert sum(d["diagnostic_deficit_mw"]) == pytest.approx(10)
    assert d["diagnostic_slack_sum_mw"] == pytest.approx(30)
    assert net.tolist() == [-30.0, 50.0] and upper.tolist() == [0.0, 30.0]
    with pytest.raises(ValueError):
        diagnostic_slack([None, 50], upper, inc, [[1]], [-10], [10])


def test_stop_pair_keeps_baseline_change_distinct_from_removed_change():
    rows = []
    for case, before, removed in [
        ("stop", "infeasible", "infeasible"),
        ("stop__record_held_reference", "feasible", "feasible"),
    ]:
        rows.append(
            dict(
                supply_trial_scenario=case,
                delivery_date="2025-04-01",
                period=1,
                entity_code="jera",
                network_scenario="saved_plan_constraints",
                all_firms_status=before,
                removed_simultaneous_status=removed,
            )
        )
    p = pair_simultaneous_conditions(pd.DataFrame(rows))
    assert p.baseline_became_feasible.all()
    assert not p.removed_failure_to_feasible_comparable.any()
    assert len(p) == 1


def test_actual_all_times_retained_and_fixed_nuclear_not_in_scope():
    root = Path(__file__).resolve().parents[1]
    p = root / "data/interim/occto/april_constraint_diagnosis/20261027_constraints_v2"
    if not (p / "manifest.json").exists():
        pytest.skip("ローカル固定比較版が必要")
    a = pd.read_parquet(p / "condition_consistency_all_times.parquet")
    assert len(a) == 23040 and a.groupby(["entity_code", "network_scenario"]).size().eq(1440).all()
    b = pd.read_parquet(p / "constraint_release_simultaneous_all_times.parquet")
    assert b.groupby(["release_case", "entity_code"]).size().eq(1440).all()
    assert b.entity_code.nunique() == 4 and b.release_case.nunique() == 14
    c = pd.read_parquet(p / "focus_regional_supply_scope_all_times.parquet")
    assert c.groupby(["entity_code", "supply_trial_scenario", "area"]).size().eq(1440).all()
    assert (
        (c.registry_nonfixed_labels - c.observed_nonfixed_labels)
        .eq(c.registry_labels_without_observation)
        .all()
    )
    assert c.remaining_upper_mw.eq(c.upper_limited_mw - c.removed_target_upper_mw).all()


def test_supply_scope_separates_known_reductions_from_record_held_scope():
    from decimal import Decimal

    from src.april_constraint_diagnosis import decompose_supply_scope

    rows = []
    for case, value, other in [
        ("reference_no_deduction", 100, None),
        ("stop", 60, 20),
        ("stop__record_held_reference", 80, None),
    ]:
        rows.append(
            dict(
                supply_trial_scenario=case,
                area="北海道",
                delivery_date="2025-04-01",
                period=1,
                entity_code="hokkaido",
                remaining_upper_mw=Decimal(value),
                other_reference_return_mw=Decimal(other) if other is not None else None,
            )
        )
    result = decompose_supply_scope(pd.DataFrame(rows))
    assert len(result) == 1
    assert result.total_reduction_other_mw.iloc[0] == 40
    assert result.known_numeric_scenario_reduction_other_mw.iloc[0] == 20
    assert result.other_reference_return_mw.iloc[0] == 20
