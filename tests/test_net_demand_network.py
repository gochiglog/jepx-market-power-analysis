"""純需要の会計・二重計上・負の純需要・フェンス係数の検証。"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.net_demand_network import (
    EDGES,
    FENCE_COEFFICIENTS,
    FixedOutputContract,
    account_balance,
    compile_legacy_constraints,
    solve_fixed_output,
)


def contract(**kwargs):
    data = dict(
        same_plan_stage=True,
        grid_boundary_aligned=True,
        self_consumption_not_subtracted_twice=True,
        overlap_resolved=True,
        fixed_asset_ids=frozenset({"R"}),
        adjustable_asset_ids=frozenset({"g"}),
    )
    return FixedOutputContract(**(data | kwargs))


def test_two_expressions_equal_in_same_mw_and_boundary():
    result = account_balance(
        Decimal("100.25"), Decimal("20.25"), Decimal("70"), Decimal("10"), contract()
    )
    assert result["net_demand_mw"] == 80
    assert result["gross_balance_residual_mw"] == result["net_balance_residual_mw"] == 0
    assert result["expression_difference_mw"] == 0
    # 同じ残差になることと、需給が成立することは別。
    unmatched = account_balance(100, 20, 50, 0, contract())
    assert unmatched["gross_balance_residual_mw"] == unmatched["net_balance_residual_mw"] == 30


@pytest.mark.parametrize(
    "problem",
    [
        "same_plan_stage",
        "grid_boundary_aligned",
        "self_consumption_not_subtracted_twice",
        "overlap_resolved",
    ],
)
def test_unconfirmed_contract_is_held(problem):
    result = account_balance(100, 20, 80, 0, contract(**{problem: False}))
    assert result["status"] == "held" and result["net_demand_mw"] is None


def test_actual_asset_overlap_prevents_double_counting():
    result = account_balance(100, 20, 80, 0, contract(adjustable_asset_ids=frozenset({"R"})))
    assert "fixed_adjustable_asset_overlap" in result["reasons"]


def test_unknown_R_is_not_zero_and_nonpositive_net_not_clipped():
    missing = account_balance(100, None, 80, 0, contract())
    assert missing["net_demand_mw"] is None
    assert missing["reasons"] == ["missing_fixed_supply"]
    for R in [100, 120]:
        result = account_balance(100, R, 0, 100 - R, contract())
        assert result["net_demand_mw"] == 100 - R
        assert result["ratio_status"] == "held_nonpositive_net_demand"


def test_net_and_gross_lambda_are_different_with_fixed_R():
    args = ({"A": 100}, {"A": 20}, {"A": 100}, [], [])
    net = solve_fixed_output(
        *args, contract=contract(), network_ready=True, target="A", scale_basis="net"
    )
    gross = solve_fixed_output(
        *args, contract=contract(), network_ready=True, target="A", scale_basis="gross"
    )
    assert net["value"] == pytest.approx(1.25)
    assert gross["value"] == pytest.approx(1.2)
    assert net["max_net_residual_mw"] < 1e-8 and gross["max_gross_residual_mw"] < 1e-8
    baseline = solve_fixed_output(*args, contract=contract(), network_ready=True)
    assert baseline["max_net_residual_mw"] == baseline["max_gross_residual_mw"] == 0


def test_negative_net_injection_must_be_exported_not_curtailed_implicitly():
    args = ({"A": 100, "B": 100}, {"A": 120, "B": 0}, {"A": 0, "B": 80}, [("A", "B")])
    c = [dict(coefficients=(1,), lower_mw=-50, upper_mw=50)]
    answer = solve_fixed_output(*args, c, contract=contract(), network_ready=True)
    assert answer["status"] == "feasible_supplied_scenario"
    assert answer["flows_mw"] == [20]
    assert answer["max_gross_residual_mw"] == answer["max_net_residual_mw"] == 0
    c[0]["upper_mw"] = 0
    assert solve_fixed_output(*args, c, contract=contract(), network_ready=True)[
        "status"
    ].startswith("infeasible")
    assert (
        solve_fixed_output(*args, c, contract=contract(), network_ready=True, target="A")["status"]
        == "held_nonpositive_ratio_denominator"
    )


def test_network_and_missing_supply_gates_remain_held():
    args = ({"A": 100}, {"A": None}, {"A": 100}, [], [])
    assert (
        solve_fixed_output(*args, contract=contract(), network_ready=True)["status"]
        == "held_missing_accounting_input"
    )
    assert (
        solve_fixed_output(*args, contract=contract(), network_ready=False)["status"]
        == "held_network"
    )
    negative = ({"A": 100}, {"A": 0}, {"A": -2}, [], [])
    assert (
        solve_fixed_output(*negative, contract=contract(), network_ready=True)["status"]
        == "held_negative_supply_input"
    )


def source_rows():
    result = []
    for name in [e[0] for e in EDGES] + ["北陸フェンス"]:
        for direction, sign in [("順方向", 1), ("逆方向", -1)]:
            result.append(
                dict(
                    link_name_raw=name,
                    direction_raw=direction,
                    operating_capacity_mw=Decimal(sign * 100),
                    margin_mw=Decimal(sign * 10),
                    wide_area_adjustment_mw=Decimal(sign * 5),
                    source_record_id=f"{name}/{direction}",
                )
            )
    return result


def test_legacy_keeps_nine_areas_and_all_individual_and_fence_constraints():
    result = compile_legacy_constraints(
        source_rows(), datetime(2026, 3, 13, tzinfo=ZoneInfo("Asia/Tokyo"))
    )
    assert result["status"] == "compiled_saved_plan_legacy"
    assert len(result["constraints"]) == 11
    fence = result["constraints"][-1]
    assert fence["coefficients"] == FENCE_COEFFICIENTS["北陸フェンス"]
    assert fence["coefficients"][4] == 1 and fence["coefficients"][5] == -1
    assert fence["lower_mw"] == -85 and fence["upper_mw"] == 85
    assert len(set(a for _, s, r in EDGES for a in [s, r])) == 9
    assert FENCE_COEFFICIENTS["中部フェンス"][3:6] == (1, 1, 0)
    assert FENCE_COEFFICIENTS["関西フェンス"][3:6] == (1, 0, 1)


def test_cutover_unresolved_not_silently_dropped_and_duplicates_rejected():
    boundary = datetime(2026, 3, 13, 0, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert compile_legacy_constraints(source_rows(), boundary)["status"] == "held_cutover_scope"
    before = datetime(2025, 4, 1, 0, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    with pytest.raises(ValueError, match="重複"):
        compile_legacy_constraints(source_rows() + [source_rows()[0]], before)
    assert (
        compile_legacy_constraints(source_rows()[:-1], before)["status"]
        == "held_missing_constraint"
    )
    extra = source_rows() + [dict(link_name_raw="新規未確認フェンス", direction_raw="順方向")]
    assert compile_legacy_constraints(extra, before)["status"] == "held_extra_constraint_scope"


def test_signed_negative_envelope_is_not_floored_or_absolutized():
    rows = source_rows()
    rows[0]["operating_capacity_mw"] = Decimal(1)
    result = compile_legacy_constraints(
        rows, datetime(2025, 4, 1, 0, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    )
    assert result["constraints"][0]["upper_mw"] == -14
    assert result["constraints"][0]["lower_mw"] == -85


def test_real_ledger_preserves_unknown_R_and_compiled_constraint_source_uniqueness():
    import json
    from pathlib import Path

    import pyarrow.parquet as pq

    output = (
        Path(__file__).resolve().parents[1]
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
    )
    if not output.exists():
        pytest.skip("Git管理外の診断版がない環境")
    ledger = pq.read_table(output / "net_demand_input_ledger_FY2025.parquet")
    assert len(ledger) == 157680
    for col in ["fixed_output_forecast_mw", "net_demand_forecast_mw", "net_demand_nonpositive"]:
        assert ledger[col].null_count == len(ledger)
    frame = ledger.select(["area", "delivery_date", "period"]).to_pandas()
    assert not frame.duplicated().any()
    assert frame.groupby(["area", "delivery_date"]).size().eq(48).all()
    assert set(frame.period) == set(range(1, 49))
    assert str(frame.delivery_date.min()) == "2025-04-01"
    assert str(frame.delivery_date.max()) == "2026-03-31"
    bounds = pq.read_table(output / "legacy_network_constraints_FY2025.parquet").to_pandas()
    assert len(bounds) == 16608 * 11
    assert not bounds.duplicated(["delivery_date", "period", "constraint_name"]).any()
    references = [r for cell in bounds.source_ids_json for r in json.loads(cell)]
    assert len(references) == len(set(references)) == 16608 * 22
    north = bounds[bounds.constraint_name == "北陸フェンス"]
    assert north.coefficients_json.map(
        lambda value: tuple(json.loads(value)) == FENCE_COEFFICIENTS["北陸フェンス"]
    ).all()
