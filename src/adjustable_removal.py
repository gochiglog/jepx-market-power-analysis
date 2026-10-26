"""固定Rを残す地域別純需要倍率。原TCRSI・正式RSIではない。"""

import numpy as np
from scipy.optimize import linprog

RECORD_HELD = {
    "held_invalid_dates",
    "held_unknown_recovery",
    "held_update_candidates",
    "held_multiple_contents",
    "held_unknown_record_type",
    "held_missing_mean_reduction",
    "held_invalid_mean_reduction",
    "held_partial_start_cell",
}


def trial_upper(original, reference, state, *, restore_record_held):
    """記録保留だけの承認済み比較。元値・負値・容量不明は修正しない。"""
    restored = (
        restore_record_held and state in RECORD_HELD and reference is not None and reference > 0
    )
    return (reference if restored else original), bool(restored)


def maximum_multiplier(
    net, upper, target, incidence, coefficients, lower, ceiling, *, constrained=True, tolerance=1e-6
):
    """他地域N固定、対象Nだけlambda倍。lambda=0の可否で早期棄却しない。"""
    net, upper = np.asarray(net, float), np.asarray(upper, float)
    if not all(np.isfinite(x).all() for x in (net, upper)):
        return {"status": "missing_input", "multiplier": None}
    if net[target] <= 0:
        return {"status": "nonpositive_target_net", "multiplier": None}
    if (upper < 0).any():
        return {"status": "negative_upper_held", "multiplier": None}
    if not constrained:
        # 全9節点が無損失・制約なしで接続された同じ輸送モデルの解析解。
        value = (upper.sum() - (net.sum() - net[target])) / net[target]
        if value < 0:
            return {"status": "other_regions_infeasible", "multiplier": None}
        return {"status": "valid", "multiplier": float(value)}
    n, m = incidence.shape
    coeff = np.asarray(coefficients, float)
    lo, hi = np.asarray(lower, float), np.asarray(ceiling, float)
    if (
        not np.isfinite(coeff).all()
        or not np.isfinite(lo).all()
        or not np.isfinite(hi).all()
        or (lo > hi).any()
    ):
        return {"status": "missing_network_input", "multiplier": None}
    matrix = np.column_stack([np.eye(n), incidence, np.zeros(n)])
    matrix[target, -1] = -net[target]
    rhs = net.copy()
    rhs[target] = 0
    ub = np.column_stack([np.zeros((len(lo), n)), coeff, np.zeros(len(lo))])
    aub = np.vstack([ub, -ub])
    bub = np.r_[hi, -lo]
    bounds = [(0, float(v)) for v in upper] + [(None, None)] * m + [(0, None)]
    objective = np.zeros(n + m + 1)
    objective[-1] = -1
    answer = linprog(
        objective, A_ub=aub, b_ub=bub, A_eq=matrix, b_eq=rhs, bounds=bounds, method="highs"
    )
    if not answer.success:
        return {
            "status": "other_regions_infeasible" if answer.status == 2 else "numerical_failure",
            "multiplier": None,
        }
    error = max(
        float(np.abs(matrix @ answer.x - rhs).max()),
        max(0.0, float((aub @ answer.x - bub).max())),
        max(0.0, float((-answer.x[:n]).max())),
        max(0.0, float((answer.x[:n] - upper).max())),
    )
    if error > tolerance:
        raise ValueError("倍率LP残差超過")
    return {
        "status": "valid",
        "multiplier": float(answer.x[-1]),
        "residual_mw": error,
        "generation_mw": answer.x[:n].tolist(),
        "flows_mw": answer.x[n : n + m].tolist(),
    }
