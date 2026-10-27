"""固定済みLPの呼出し監査。研究定義や旧計算を変更しない。"""

from unittest.mock import patch

import numpy as np
from scipy.optimize import linprog


def capture_solver_call(function, *args, **kwargs):
    """実関数がHiGHSへ渡す入力を捕捉し、同じ呼出しを実行する。"""
    calls = []

    def spy(c, **options):
        calls.append(
            {
                "objective": np.asarray(c).tolist(),
                **{k: np.asarray(options[k]).tolist() for k in ("A_eq", "b_eq", "A_ub", "b_ub")},
                "bounds": options["bounds"],
                "method": options["method"],
            }
        )
        return linprog(c, **options)

    with patch(function.__module__ + ".linprog", side_effect=spy):
        result = function(*args, **kwargs)
    if len(calls) != 1:
        raise ValueError("監査対象はソルバーを1回呼ぶ実入力に限定")
    return result, calls[0]


def minimum_feasible_multiplier(call):
    """同じ実行制約でlambda下端を点検。新指標の採用ではない。"""
    c = np.zeros(len(call["objective"]))
    c[-1] = 1
    answer = linprog(
        c, **{k: call[k] for k in ("A_eq", "b_eq", "A_ub", "b_ub", "bounds", "method")}
    )
    return {
        "status": int(answer.status),
        "minimum_lambda": float(answer.fun) if answer.success else None,
    }


def apply_two_asset_extension(regional_upper, unit_values_kw):
    """定義済み非負上限だけを追加。不明値は未計算件数へ保持する。"""
    if regional_upper is None:
        raise ValueError("地域全不能を0にしない")
    known = [v for v in unit_values_kw if v is not None and v >= 0]
    from decimal import Decimal

    increment = sum(known, Decimal(0)) / Decimal(1000)
    return regional_upper + increment, increment, len(known), len(unit_values_kw) - len(known)
