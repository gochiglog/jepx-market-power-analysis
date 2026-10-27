"""固定年間結果を排他的3区分へ整理する。LP・状態の上書きは行わない。"""

import numpy as np
import pandas as pd

KEY = ["entity_code", "delivery_date", "period"]
COMMON = "common_all_firms_feasible"
CATEGORIES = [
    "feasible_with_saved_bounds",
    "feasible_only_without_bounds",
    "infeasible_even_without_bounds",
]
LABELS = ["除去後も保存枠下で成立", "保存枠下で不成立だが枠解除で成立", "枠を解除しても不成立"]


def three_categories(frame):
    """両条件を1対1照合し全時刻を保持。未定義・矛盾は0分類しない。"""
    if frame.duplicated(KEY + ["network_scenario"]).any():
        raise ValueError("元結果のキー重複")
    if set(frame.network_scenario) != {"saved_plan_constraints", "no_transmission_bounds"}:
        raise ValueError("送電比較の条件が不足")
    original_columns = ["all_firms_status", "removed_simultaneous_status"]
    sides = []
    for ns, prefix in [("saved_plan_constraints", "saved"), ("no_transmission_bounds", "free")]:
        f = frame[frame.network_scenario.eq(ns)][KEY + original_columns].copy()
        sides.append(f.rename(columns={c: prefix + "__" + c for c in original_columns}))
    joined = sides[0].merge(sides[1], on=KEY, how="outer", validate="one_to_one", indicator=True)
    if not joined._merge.eq("both").all():
        raise ValueError("比較元の時刻が未一致")
    joined = joined.drop(columns="_merge")
    ba = joined.saved__all_firms_status
    bf = joined.free__all_firms_status
    ready = ba.eq("feasible") & bf.eq("feasible")
    joined["comparison_scope"] = np.select(
        [ready, ba.eq("held_network_input"), ba.eq("infeasible") | bf.eq("infeasible")],
        [COMMON, "held_network_input", "all_firms_infeasible"],
        default="other_held",
    )
    a = joined.saved__removed_simultaneous_status
    b = joined.free__removed_simultaneous_status
    if not (
        a[ready].isin(["feasible", "infeasible"]).all()
        and b[ready].isin(["feasible", "infeasible"]).all()
    ):
        raise ValueError("共通母集団の除去後判定が不明")
    if (ready & a.eq("feasible") & b.eq("infeasible")).any():
        raise ValueError("包絡解除の包含関係に矛盾")
    joined["central_category"] = pd.Series(None, index=joined.index, dtype="object")
    joined.loc[ready & a.eq("feasible"), "central_category"] = CATEGORIES[0]
    joined.loc[ready & a.eq("infeasible") & b.eq("feasible"), "central_category"] = CATEGORIES[1]
    joined.loc[ready & b.eq("infeasible"), "central_category"] = CATEGORIES[2]
    if joined.loc[ready, "central_category"].isna().any():
        raise ValueError("共通母集団の分類漏れ")
    return joined


def describe(series):
    """表示統計だけfloatを使用。元Decimal列は変更しない。"""
    x = series.dropna().astype(float)
    return dict(
        observed=len(x),
        missing=len(series) - len(x),
        minimum=x.min(),
        q25=x.quantile(0.25),
        median=x.median(),
        q75=x.quantile(0.75),
        maximum=x.max(),
    )


def artificial_fence_example(south, east):
    """全て説明用人工値：各区間±100、北陸フェンス±60 MW。"""
    return dict(
        S=south,
        E=east,
        F_H=south - east,
        individual_ok=abs(south) <= 100 and abs(east) <= 100,
        fence_ok=abs(south - east) <= 60,
    )
