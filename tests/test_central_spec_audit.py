"""実LPの行列捕捉・固定出力の例外・非補完の確認。"""

from decimal import Decimal

import numpy as np
import pytest

from src.adjustable_removal import maximum_multiplier, trial_upper
from src.april_trial_comparison import simultaneous_feasibility
from src.central_spec_audit import (
    apply_two_asset_extension,
    capture_solver_call,
    minimum_feasible_multiplier,
)


def test_real_function_call_matrix_retains_net_and_no_diagnostic_slack():
    incidence = np.array([[-1.0], [1.0]])
    c = np.array([[1.0]])
    result, call = capture_solver_call(
        maximum_multiplier, [10, 20], [30, 20], 0, incidence, c, [-8], [8]
    )
    assert result["status"] == "valid"
    assert result["multiplier"] == pytest.approx(3)
    assert call["objective"] == [0, 0, 0, -1]
    assert call["A_eq"] == [[1, 0, -1, -10], [0, 1, 1, 0]]
    assert call["b_eq"] == [0, 20]
    assert len(call["bounds"]) == 2 + 1 + 1
    assert minimum_feasible_multiplier(call)["minimum_lambda"] == 0
    sim, scall = capture_solver_call(
        simultaneous_feasibility, [10, 20], [30, 20], incidence, c, [-8], [8]
    )
    assert sim["status"] == "feasible"
    assert len(scall["bounds"]) == 3
    assert scall["b_eq"] == [10, 20]


def test_fixed_surplus_counterexample_max_above_one_but_original_infeasible():
    # 他地域で固定Rが需要を20上回る。対象の元純需要10では吸収先不足。
    incidence = np.array([[-1.0], [1.0]])
    coeff = np.array([[1.0]])
    result, call = capture_solver_call(
        maximum_multiplier, [10, -20], [10, 0], 0, incidence, coeff, [-100], [100]
    )
    assert result["multiplier"] == pytest.approx(3)
    assert minimum_feasible_multiplier(call)["minimum_lambda"] == pytest.approx(2)
    assert (
        simultaneous_feasibility([10, -20], [10, 0], incidence, coeff, [-100], [100])["status"]
        == "infeasible"
    )
    # 現行statusゲートで全法人不成立になる例。公表有効倍率の実例とは分ける。


def test_capacity_extension_preserves_unknown_negative_and_units():
    values = [Decimal("100000"), Decimal("112000")]
    assert apply_two_asset_extension(Decimal("250"), values) == (
        Decimal("462"),
        Decimal("212"),
        2,
        0,
    )
    assert apply_two_asset_extension(Decimal("250"), [None, Decimal("-100")]) == (
        Decimal("250"),
        Decimal("0"),
        0,
        2,
    )
    with pytest.raises(ValueError):
        apply_two_asset_extension(None, values)


def test_existing_record_rules_apply_to_external_evidence_candidates():
    assert trial_upper(
        None, Decimal("100000"), "held_unknown_recovery", restore_record_held=False
    ) == (None, False)
    assert trial_upper(
        None, Decimal("100000"), "held_unknown_recovery", restore_record_held=True
    ) == (Decimal("100000"), True)
    assert trial_upper(
        None, Decimal("100000"), "held_capacity_period", restore_record_held=True
    ) == (None, False)


def test_unit_aggregation_equivalent_only_for_independent_zero_lower_bounds():
    # 同地域の独立した[0,10],[0,20]は[0,30]。0〜30を全て分解できる。
    for total in [0, 5, 10, 11, 25, 30]:
        unit1 = min(10, total)
        unit2 = total - unit1
        assert 0 <= unit1 <= 10 and 0 <= unit2 <= 20
        assert unit1 + unit2 == total
    # 最低出力/熱供給/ランプ/契約があればこの集約条件を満たすとはしない。
    assert not (20 <= 5 <= 30)
