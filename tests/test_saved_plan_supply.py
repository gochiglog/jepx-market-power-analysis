from decimal import Decimal

import numpy as np
import pyarrow as pa
import pytest

from src.saved_plan_supply import (
    CODE,
    Edge,
    Fence,
    adjust_upper,
    context_policies,
    decimal_to_micro,
    evaluate_named_removal,
    micro_to_decimal,
    remove_reference_group,
    solve_transport,
)


def test_decimal_units_preserve_null_and_negative():
    source = pa.array(
        [Decimal("100.123456"), None, Decimal("-0.000001")], type=pa.decimal128(28, 6)
    )
    values, valid = decimal_to_micro(source)
    assert values.tolist() == [100123456, 0, -1]
    assert valid.tolist() == [True, False, True]
    assert micro_to_decimal(values, valid).equals(source)
    assert micro_to_decimal(values, valid, mw=True).to_pylist() == [
        Decimal("0.100123456"),
        None,
        Decimal("-0.000000001"),
    ]


def example(kind="計画停止", mean=Decimal(20), full=True, copies=1, **flags):
    ctx = dict(
        context_id=1,
        has_date_boundary=False,
        has_invalid_dates=False,
        has_unknown_recovery=False,
        has_update_candidates=False,
        has_multiple_contents=False,
        original_row_count=copies,
        distinct_content_count=1,
        member_content_ids=["a"],
        full_scenario_content_ids=["a"] if full else [],
    )
    ctx.update(flags)
    row = {"停止日時": "2025/04/01 00:00", "停止区分": kind, "published_mean_reduction_kw": mean}
    return [ctx], {"a": row}


def test_stop_zero_and_mean_are_distinct_no_generation_subtraction():
    ctx, content = example()
    args = np.array([100000000]), np.array([True]), np.array([1]), np.array([1])
    zero = adjust_upper(*args, context_policies(ctx, content, "full_stop_zero"))
    mean = adjust_upper(*args, context_policies(ctx, content, "mean_reduction"))
    assert zero[0][0] == 0 and zero[1][0]
    assert mean[0][0] == 80000000 and mean[1][0]
    assert remove_reference_group([0, 80], ["i", "j"], "i")["removed"] == 0
    assert remove_reference_group([0, 80], ["i", "j"], "i")["remaining"].tolist() == [0, 80]


@pytest.mark.parametrize(
    "flag",
    ["has_unknown_recovery", "has_update_candidates", "has_multiple_contents", "has_invalid_dates"],
)
def test_unresolved_records_are_never_chosen(flag):
    ctx, content = example(**{flag: True})
    out = adjust_upper(
        np.array([100000000]),
        np.array([True]),
        np.array([1]),
        np.array([1]),
        context_policies(ctx, content, "full_stop_zero"),
    )
    assert not out[1][0]


def test_exact_copies_apply_mean_once_and_negative_is_retained():
    ctx, content = example(kind="出力低下", mean=Decimal(120), copies=3)
    out = adjust_upper(
        np.array([100000000]),
        np.array([True]),
        np.array([1]),
        np.array([1]),
        context_policies(ctx, content, "mean_reduction"),
    )
    assert out[0][0] == -20000000 and out[1][0]
    assert out[2][0] == CODE["negative_adjusted_upper_retained"]
    assert out[4][0] == 2
    assert (
        remove_reference_group([-20, 80], ["i", "j"], "i")["status"]
        == "held_invalid_adjusted_supply"
    )


def test_early_late_boundary_and_partial_start_are_explicit():
    early_ctx, content = example(full=False, has_date_boundary=True)
    late_ctx, _ = example(full=True, has_date_boundary=True)
    args = np.array([100000000]), np.array([True]), np.array([1]), np.array([48])
    early = adjust_upper(*args, context_policies(early_ctx, content, "full_stop_zero"))
    late = adjust_upper(*args, context_policies(late_ctx, content, "full_stop_zero"))
    assert early[0][0] == 100000000 and late[0][0] == 0
    assert early[3][0] and late[3][0]
    content["a"]["停止日時"] = "2025/04/01 00:15"
    partial = adjust_upper(
        args[0],
        args[1],
        args[2],
        np.array([0]),
        context_policies(late_ctx, content, "full_stop_zero"),
    )
    assert not partial[1][0]


def test_record_absent_is_assumed_no_deduction_not_availability():
    policies = (
        np.array([0], np.uint8),
        np.array([0]),
        np.array([-1]),
        np.array([False]),
        np.array([0]),
    )
    out = adjust_upper(np.array([100]), np.array([True]), np.array([0]), np.array([1]), policies)
    assert out[0][0] == 100 and out[2][0] == CODE["no_additional_deduction_record_absent"]
    assert remove_reference_group([10], ["other"], "EP")["removed"] is None


def test_artificial_two_region_lp_baseline_then_removal_sender_demand():
    edge = Edge("A", "B", 60, 0)
    demand = {"A": 100, "B": 100}
    baseline = solve_transport(demand, {"A": 160, "B": 60}, [edge])
    assert baseline["status"] == "feasible_reference_scenario"
    assert baseline["max_balance_error_mw"] < 1e-7
    removed = solve_transport(demand, {"A": 160, "B": 0}, [edge])
    assert removed["status"].startswith("infeasible")
    ratio = solve_transport(demand, {"A": 160, "B": 0}, [edge], target="B")
    assert ratio["value"] == pytest.approx(0.6)
    assert ratio["generation_mw"][0] == pytest.approx(160)
    assert solve_transport(demand, {"A": 160, "B": 0}, [Edge("B", "A", 60, 0)])[
        "status"
    ].startswith("infeasible")


def test_artificial_fence_and_nine_region_gate():
    demand = {str(i): 10 for i in range(9)}
    supply = {str(i): 10 for i in range(9)}
    edges = [Edge(str(i), str(i + 1), 30, 30) for i in range(8)]
    assert solve_transport(demand, supply, edges)["status"] == "feasible_reference_scenario"
    assert solve_transport(demand, supply, edges, network_complete=False)["value"] is None
    fence = Fence((1,), -100, 20)
    answer = solve_transport(
        {"A": 100, "B": 100},
        {"A": 200, "B": 0},
        [Edge("A", "B", 100, 0)],
        fences=[fence],
        target="B",
    )
    assert answer["value"] == pytest.approx(0.2)
    assert (
        solve_transport({"A": 100, "B": 100}, {"A": -1, "B": 200}, [Edge("A", "B", 100, 0)])[
            "status"
        ]
        == "held_negative_supply"
    )


def test_baseline_failure_is_distinguished_from_removal_failure():
    demand = {"A": 100, "B": 100}
    edge = Edge("A", "B", 60, 0)
    rows = [
        {"area": "A", "entity_reference": "i", "adjusted_upper_mw": 40},
        {"area": "A", "entity_reference": "j", "adjusted_upper_mw": 120},
        {"area": "B", "entity_reference": "i", "adjusted_upper_mw": 60},
    ]
    answer = evaluate_named_removal(demand, rows, "i", [edge])
    assert answer["baseline"]["status"] == "feasible_reference_scenario"
    assert answer["removed_adjusted_mw"] == 100
    assert answer["removed"]["status"].startswith("infeasible")
    rows[0]["adjusted_upper_mw"] = 0
    failed = evaluate_named_removal(demand, rows, "i", [edge])
    assert failed["comparison_status"] == "baseline_not_feasible_or_defined"
    assert failed["removed"] is None


def test_missing_reduction_and_capacity_are_held_not_zero():
    ctx, content = example(mean=None)
    answer = adjust_upper(
        np.array([100000000, 0]),
        np.array([True, False]),
        np.array([1, 1]),
        np.array([1, 1]),
        context_policies(ctx, content, "mean_reduction"),
    )
    assert not answer[1].any()
    assert answer[2].tolist() == [
        CODE["held_missing_mean_reduction"],
        CODE["held_reference_capacity"],
    ]


def test_real_saved_plan_baseline_uses_nine_regions_without_generation_deduction():
    from pathlib import Path

    import pyarrow.parquet as pq

    output = (
        Path(__file__).resolve().parents[1]
        / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1"
    )
    if not (output / "regional_all_suppliers.parquet").exists():
        pytest.skip("Git管理外の診断版がない環境")
    filters = [("supply_scenario", "=", "reference_no_deduction")]
    regional = pq.read_table(
        output / "regional_all_suppliers.parquet",
        filters=filters,
        columns=[
            "area", "delivery_date", "period", "reference_capacity_known_partial_mw",
            "adjusted_upper_partial_mw", "area_demand_forecast_mw", "availability_verified",
        ],
    ).to_pandas()
    national = pq.read_table(
        output / "national_nine_areas_all_suppliers.parquet",
        filters=filters,
        columns=["delivery_date", "period", "adjusted_upper_partial_mw"],
    ).to_pandas()
    assert len(regional) == 10 * 17520 and len(national) == 17520
    assert not regional.duplicated(["area", "delivery_date", "period"]).any()
    assert regional.groupby(["area", "delivery_date"]).size().eq(48).all()
    assert not regional.availability_verified.any()
    assert regional.reference_capacity_known_partial_mw.equals(regional.adjusted_upper_partial_mw)
    nine = regional[regional.area != "沖縄"]
    grouped = nine.groupby(["delivery_date", "period"]).adjusted_upper_partial_mw.sum()
    expected = national.set_index(["delivery_date", "period"]).adjusted_upper_partial_mw
    assert grouped.equals(expected.sort_index())
    assert len(nine) == 9 * 17520
