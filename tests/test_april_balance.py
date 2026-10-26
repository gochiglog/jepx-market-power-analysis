import numpy as np
import pytest

from src.april_balance import (
    signed_storage_account,
    solve_balance,
    validate_regional_header,
)


def two_node(net, upper, limit):
    return solve_balance(
        net, upper, np.array([[-1.0], [1.0]]), np.array([[1.0]]), [-limit], [limit]
    )


def test_balanced_trade_and_signed_surplus():
    answer = two_node([-20, 50], [0, 40], 30)
    assert answer["status"] == "feasible"
    assert answer["flows_mw"][0] == pytest.approx(20)
    assert answer["generation_mw"][1] == pytest.approx(30)
    assert answer["max_residual_mw"] < 1e-6


def test_fixed_surplus_is_not_clipped():
    answer = two_node([-50, 20], [0, 0], 100)
    assert answer["status"] == "infeasible"
    assert answer["national_fixed_surplus_mw"] == 30
    assert sum(answer["diagnostic_surplus_mw"]) == pytest.approx(30)
    assert not answer["network_relaxed_feasible"]


def test_supply_shortfall_is_diagnostic_only():
    answer = two_node([100, 100], [90, 90], 100)
    assert answer["national_upper_shortfall_mw"] == 20
    assert sum(answer["diagnostic_deficit_mw"]) == pytest.approx(20)
    assert "generation_mw" not in answer


def test_network_block_is_separate_from_national_capacity():
    answer = two_node([10, 90], [150, 0], 50)
    assert answer["status"] == "infeasible"
    assert answer["network_relaxed_feasible"]
    assert answer["national_upper_shortfall_mw"] == 0
    assert sum(answer["diagnostic_deficit_mw"]) == pytest.approx(40)


def test_unknown_and_negative_upper_are_held():
    assert two_node([10, np.nan], [20, 20], 10)["status"] == "held_missing_input"
    assert two_node([10, 10], [-1, 20], 10)["status"] == "held_invalid_bound"


def test_fence_is_simultaneous_constraint():
    incidence = np.array([[-1, 0], [1, -1], [0, 1]])
    answer = solve_balance(
        [0, 0, 100],
        [200, 0, 0],
        incidence,
        np.array([[1, 0], [0, 1], [1, 1]]),
        [-200, -200, -100],
        [200, 200, 100],
    )
    assert answer["status"] == "infeasible"
    assert answer["network_relaxed_feasible"]


def test_region_header_rejects_wrong_filename_and_duplicates():
    validate_regional_header("北海道", ["北海道", "", ""])
    with pytest.raises(ValueError, match="不一致"):
        validate_regional_header("東北", ["北海道", ""])
    with pytest.raises(ValueError):
        validate_regional_header("北海道", ["北海道", "東北"])


def test_storage_unknown_is_not_zero_or_gross_charge():
    assert signed_storage_account(None, 0) is None
    assert signed_storage_account(-100, 20) == -80
    assert signed_storage_account(0, 0) == 0


def test_real_april_saved_allocations_obey_all_nine_region_balances():
    """実際の割当を別読取で会計・全11制約へ再照合する。"""
    import json
    from pathlib import Path

    import pandas as pd

    from src.net_demand_network import AREAS, EDGES

    root = Path(__file__).resolve().parents[1]
    folder = root / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
    if not folder.exists():
        pytest.skip("手元の研究データを必要とするintegration test")
    ledger = pd.read_parquet(folder / "B30_primary_input_ledger.parquet").set_index(
        ["area", "delivery_date", "period"]
    )
    upper = pd.read_parquet(folder / "limited_supply_regional_inputs.parquet").set_index(
        ["supply_scenario", "area", "delivery_date", "period"]
    )
    allocations = pd.read_parquet(folder / "feasible_allocations.parquet")
    results = pd.read_parquet(folder / "all_firm_balance_results.parquet")
    assert len(ledger) == 12960 and len(results) == 12960
    assert results.status.isin(["feasible", "infeasible"]).all()
    assert len(allocations) == int(results.status.eq("feasible").sum())
    assert not results.firm_removal_applied.any()
    assert (
        not ledger[["demand_actual_sample_mw", "fixed_actual_mw", "net_demand_B_mw"]]
        .isna()
        .any()
        .any()
    )
    assert ledger.net_demand_B_mw.eq(ledger.demand_actual_sample_mw - ledger.fixed_actual_mw).all()
    network = pd.read_parquet(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet"
    )
    grouped = {(d, p): f for (d, p), f in network.groupby(["delivery_date", "period"])}
    incidence = np.zeros((9, 10))
    for i, (_, sender, receiver) in enumerate(EDGES):
        incidence[AREAS.index(sender), i] = -1
        incidence[AREAS.index(receiver), i] = 1
    for row in allocations.itertuples():
        key = [(a, row.delivery_date, row.period) for a in AREAS]
        net = ledger.loc[key].net_demand_B_mw.to_numpy(float)
        u = upper.loc[[(row.supply_scenario, *k) for k in key]].limited_known_upper_mw.to_numpy(
            float
        )
        g = np.array(json.loads(row.generation_mw_by_area_json))
        flow = np.array(json.loads(row.flow_mw_in_EDGES_order_json))
        assert np.max(np.abs(g + incidence @ flow - net)) < 1e-6
        assert (g >= -1e-6).all() and (g <= u + 1e-6).all()
        constraints = grouped[row.delivery_date, row.period]
        coefficients = np.array([json.loads(v) for v in constraints.coefficients_json])
        value = coefficients @ flow
        assert (value >= constraints.lower_mw.to_numpy(float) - 1e-6).all()
        assert (value <= constraints.upper_mw.to_numpy(float) + 1e-6).all()
