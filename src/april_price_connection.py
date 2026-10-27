"""固定済み4月試行への記述用価格接続。因果推論・入力修正は行わない。"""

import numpy as np
import pandas as pd

STATES = (
    "all17_base_feasible_removed_infeasible",
    "all17_base_feasible_removed_feasible",
    "removed_varies_among_baseline_feasible",
    "baseline_scope_differs",
    "other_held",
)
LABELS = (
    "①除去後不成立で一致",
    "②除去後成立で一致",
    "③条件により変化",
    "④全法人不成立を含む",
    "⑤その他保留",
)


def require_join(left, right, keys, *, validate, suffixes=("_left", "_right")):
    """母集団を落とさず、期待するキー対応を強制する。"""
    result = left.merge(
        right,
        on=keys,
        how="left",
        validate=validate,
        indicator="_price_join_check",
        suffixes=suffixes,
    )
    if len(result) != len(left) or not result._price_join_check.eq("both").all():
        raise ValueError("価格/状態の結合不一致または行増殖")
    return result.drop(columns="_price_join_check")


def distribution(values):
    """分布の計算だけfloat化。保存元価格は変更しない。空集合を0にしない。"""
    x = pd.to_numeric(values, errors="raise").astype(float)
    if x.isna().any() or not np.isfinite(x).all():
        raise ValueError("未知値を分布から黙って落とさない")
    if not len(x):
        return dict(count=0, minimum=None, q25=None, median=None, q75=None, maximum=None)
    return dict(
        count=len(x),
        minimum=float(x.min()),
        q25=float(x.quantile(0.25)),
        median=float(x.median()),
        q75=float(x.quantile(0.75)),
        maximum=float(x.max()),
    )


def signed_transfer_envelope(capacity, margin, wide_adjustment, planned, available):
    """符号軸を維持。追加空容量と全体再配分包絡を概念上区別する。"""
    upper_or_lower = capacity - margin - wide_adjustment
    return dict(
        model_bound=upper_or_lower,
        available_identity_matches=available == upper_or_lower - planned,
        interpretation="conditional_saved_net_transfer_envelope",
    )
