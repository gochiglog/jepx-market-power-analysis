"""年間用モデル再利用と既存LPの等価性。"""

import numpy as np
import pytest

from src.adjustable_removal import maximum_multiplier
from src.annual_supply_solver import A, C, TransportModel
from src.april_trial_comparison import simultaneous_feasibility


def test_reused_model_matches_original_formulation_with_fixed_surplus():
    lo = np.full(13, -500.0)
    hi = np.full(13, 500.0)
    model = TransportModel()
    models = [TransportModel(k) for k in range(9)]
    cases = [
        (np.array([100.0] * 9), np.array([200.0] * 9)),
        (
            np.array([100.0, 100.0, -20.0, 100.0, 0.0, 100.0, 100.0, 100.0, 100.0]),
            np.array([200.0] * 9),
        ),
        (np.array([100.0] * 9), np.array([1.0] * 9)),
    ]
    for n, u in cases:
        actual = model.solve(n, u, lo, hi)
        expected = simultaneous_feasibility(n, u, A, C, lo, hi)
        assert actual["status"] == expected["status"]
        for k in range(9):
            if n[k] <= 0:
                continue
            actual = models[k].solve(n, u, lo, hi)
            expected = maximum_multiplier(n, u, k, A, C, lo, hi)
            if expected["status"] == "valid":
                assert actual["value"] / n[k] == pytest.approx(expected["multiplier"], abs=1e-8)
            else:
                assert actual["status"] == "infeasible"


def test_central_fences_are_dependent_cuts_not_three_independent_capacities():
    np.testing.assert_array_equal(C[11] - C[12], C[10])
    for middle, hokuriku in [(100, 40), (-30, 50), (0, -50)]:
        f = np.zeros(10)
        f[3] = middle
        f[4] = 0
        f[5] = -hokuriku
        np.testing.assert_allclose(C[10:] @ f, [hokuriku, middle, middle - hokuriku])


def test_unknown_upper_not_zero_and_repeated_infeasible_then_feasible():
    model = TransportModel()
    n = np.full(9, 10.0)
    lo = np.full(13, -100.0)
    hi = -lo
    assert model.solve(n, np.full(9, np.nan), lo, hi)["status"] == "input_held"
    assert model.solve(n, np.zeros(9), lo, hi)["status"] == "infeasible"
    assert model.solve(n, np.full(9, 30.0), lo, hi)["status"] == "feasible"
