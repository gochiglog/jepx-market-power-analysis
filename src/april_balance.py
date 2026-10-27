"""実績D/Rと保存計画制約による全法人診断。RSI・実需給再現ではない。"""

import numpy as np
from scipy.optimize import linprog


def validate_regional_header(expected_area, region_header):
    """地域別REI原本の保存名を信用せず、明示ヘッダー不一致を拒否する。"""
    labels = {str(x).strip() for x in region_header if str(x).strip()}
    if labels != {expected_area}:
        raise ValueError(f"地域名と原ヘッダー不一致: {expected_area}: {sorted(labels)}")


def signed_storage_account(pump, battery):
    """純注入のみ。負の純値を総充電量と呼ばず、空欄を0にしない。"""
    if pump is None or battery is None:
        return None
    return pump + battery


def solve_balance(net, upper, incidence, coefficients, lower, ceiling, *, tolerance=1e-6):
    """未知は保留。限定集合内の既知上限のみを使い、過不足は診断専用。"""
    n, m = incidence.shape
    arrays = [net, upper, incidence, coefficients, lower, ceiling]
    if any(not np.isfinite(np.asarray(a, dtype=float)).all() for a in arrays):
        return {"status": "held_missing_input"}
    net, upper, lower, ceiling = [np.asarray(x, float) for x in (net, upper, lower, ceiling)]
    if len(net) != n or len(upper) != n or np.asarray(coefficients).shape[1] != m:
        raise ValueError("入力範囲不一致")
    if (upper < 0).any() or (lower > ceiling).any():
        return {"status": "held_invalid_bound"}
    matrix = np.column_stack([np.eye(n), incidence])
    inequality = np.vstack(
        [
            np.column_stack([np.zeros((len(lower), n)), coefficients]),
            np.column_stack([np.zeros((len(lower), n)), -np.asarray(coefficients)]),
        ]
    )
    limits = np.concatenate([ceiling, -lower])
    bounds = [(0, u) for u in upper] + [(None, None)] * m
    result = linprog(
        np.zeros(n + m),
        A_ub=inequality,
        b_ub=limits,
        A_eq=matrix,
        b_eq=net,
        bounds=bounds,
        method="highs",
    )
    if result.success:
        error = float(np.abs(matrix @ result.x - net).max())
        bound_error = max(0.0, float((inequality @ result.x - limits).max()))
        if max(error, bound_error) > tolerance:
            raise ValueError("LP解の残差が許容値超過")
        return {
            "status": "feasible",
            "generation_mw": result.x[:n].tolist(),
            "flows_mw": result.x[n:].tolist(),
            "max_residual_mw": error,
            "max_constraint_violation_mw": bound_error,
        }
    if result.status != 2:
        return {"status": "held_solver_failure", "solver_message": result.message}
    # g+I+不足−余剰=N。正式な供給/需要へ混ぜない。
    diagnostic_matrix = np.column_stack([matrix, np.eye(n), -np.eye(n)])
    diag = linprog(
        np.r_[np.zeros(n + m), np.ones(2 * n)],
        A_ub=np.column_stack([inequality, np.zeros((len(limits), 2 * n))]),
        b_ub=limits,
        A_eq=diagnostic_matrix,
        b_eq=net,
        bounds=bounds + [(0, None)] * (2 * n),
        method="highs",
    )
    if not diag.success:
        raise ValueError("過不足診断LP失敗")
    relaxed = linprog(np.zeros(n + m), A_eq=matrix, b_eq=net, bounds=bounds, method="highs")
    if relaxed.status not in (0, 2):
        raise ValueError("送電包絡解除診断LP失敗")
    return {
        "status": "infeasible",
        "diagnostic_deficit_mw": diag.x[n + m : n + m + n].tolist(),
        "diagnostic_surplus_mw": diag.x[n + m + n :].tolist(),
        "diagnostic_slack_sum_mw": float(diag.fun),
        "network_relaxed_feasible": bool(relaxed.success),
        "national_upper_shortfall_mw": max(0.0, float(net.sum() - upper.sum())),
        "national_fixed_surplus_mw": max(0.0, float(-net.sum())),
        "diagnostic_residual_mw": float(np.abs(diagnostic_matrix @ diag.x - net).max()),
    }
