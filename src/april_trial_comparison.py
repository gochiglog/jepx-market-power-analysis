"""固定済み地域別純需要倍率の状態比較と、倍率を使わない9地域同時成立検査。"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog

BAND = 1e-8
CATEGORIES = (
    "below_one",
    "at_least_one",
    "numerical_boundary",
    "removed_other_regions_infeasible",
    "nonpositive_target_net",
    "all_firms_infeasible",
    "other_held",
)
LABELS = {
    "below_one": "除去後倍率1未満",
    "at_least_one": "除去後倍率1以上",
    "numerical_boundary": "数値境界帯",
    "removed_other_regions_infeasible": "除去後の他地域条件不成立",
    "nonpositive_target_net": "対象純需要非正",
    "all_firms_infeasible": "全法人不成立",
    "other_held": "その他の保留／計算失敗",
}
PAIR_KEY = ["delivery_date", "period", "target_area", "entity", "network_scenario"]


def display_categories(frame):
    """既存の主statusを優先したまま、validだけを閾値で分ける。"""
    out = pd.Series("other_held", index=frame.index, dtype="str")
    for status in CATEGORIES[3:6]:
        out.loc[frame.status.eq(status)] = status
    valid = frame.status.eq("valid")
    values = frame.lambda_removed
    if not np.isfinite(values[valid].to_numpy(float)).all():
        raise ValueError("validに非有限の倍率")
    out.loc[valid & values.lt(1 - BAND)] = "below_one"
    out.loc[valid & values.gt(1 + BAND)] = "at_least_one"
    out.loc[valid & values.between(1 - BAND, 1 + BAND, inclusive="both")] = "numerical_boundary"
    if out[valid].eq("other_held").any():
        raise ValueError("閾値区分の欠落")
    return out


def ecdf_points(values):
    """同値は同じ累積割合。極端値を削らず、全有効時刻を分母にする。"""
    array = np.asarray(values, float)
    if not np.isfinite(array).all():
        raise ValueError("ECDFへ不明値を黙って落とさない")
    if not len(array):
        return np.array([]), np.array([])
    x, count = np.unique(array, return_counts=True)
    return np.r_[x[0], x], np.r_[0, np.cumsum(count) / len(array)]


def paired_stop_comparison(before, after):
    """全時刻を外部結合し、倍率差は双方validのときだけ計算。"""
    result = before.merge(
        after,
        on=PAIR_KEY,
        how="outer",
        validate="one_to_one",
        suffixes=("_before", "_after"),
        indicator=True,
    )
    if not result._merge.eq("both").all():
        raise ValueError("停止比較の時刻不一致")
    result["both_valid"] = result.status_before.eq("valid") & result.status_after.eq("valid")
    result["became_valid"] = ~result.status_before.eq("valid") & result.status_after.eq("valid")
    result["lambda_difference"] = np.where(
        result.both_valid, result.lambda_removed_after - result.lambda_removed_before, np.nan
    )
    result["below_to_above"] = (
        result.both_valid
        & result.display_category_before.eq("below_one")
        & result.display_category_after.eq("at_least_one")
    )
    result["above_to_below"] = (
        result.both_valid
        & result.display_category_before.eq("at_least_one")
        & result.display_category_after.eq("below_one")
    )
    return result


def simultaneous_feasibility(net, upper, incidence, coeff, lo, hi, *, constrained=True):
    """全地域Nを元の値に固定。未知/負上限を補わない。lambda変数なし。"""
    n, m = incidence.shape
    arrays = [np.asarray(x, float) for x in (net, upper, coeff, lo, hi)]
    net, upper, coeff, lo, hi = arrays
    if not all(np.isfinite(x).all() for x in arrays):
        return {"status": "input_held", "residual_mw": None}
    if (upper < 0).any() or (lo > hi).any():
        return {"status": "input_held", "residual_mw": None}
    if not constrained:
        # 全9地域の同じ接続で包絡のみ解除した無損失輸送。
        return {
            "status": "feasible" if 0 <= net.sum() <= upper.sum() else "infeasible",
            "residual_mw": None,
        }
    matrix = np.column_stack([np.eye(n), incidence])
    row = np.column_stack([np.zeros((len(lo), n)), coeff])
    aub = np.vstack([row, -row])
    bub = np.r_[hi, -lo]
    answer = linprog(
        np.zeros(n + m),
        A_ub=aub,
        b_ub=bub,
        A_eq=matrix,
        b_eq=net,
        bounds=[(0, float(u)) for u in upper] + [(None, None)] * m,
        method="highs",
    )
    if not answer.success:
        return {
            "status": "infeasible" if answer.status == 2 else "numerical_failure",
            "residual_mw": None,
        }
    error = max(
        float(np.abs(matrix @ answer.x - net).max()),
        max(0, float((aub @ answer.x - bub).max())),
        max(0, float((-answer.x[:n]).max())),
        max(0, float((answer.x[:n] - upper).max())),
    )
    if error > 1e-6:
        raise ValueError("同時成立LPの残差超過")
    return {
        "status": "feasible",
        "residual_mw": error,
        "generation_mw": answer.x[:n].tolist(),
        "flows_mw": answer.x[n:].tolist(),
    }
