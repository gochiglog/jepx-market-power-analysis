"""固定済み17条件の一致と、保存送電包絡の解除診断。研究定義は変更しない。"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog


def condition_consistency(group):
    """17条件を全時刻で保持。全法人不成立を除去効果に数えない。"""
    if len(group) != 17 or group.supply_trial_scenario.nunique() != 17:
        raise ValueError("17供給条件の重複または欠落")
    base = group.all_firms_status
    removed = group.removed_simultaneous_status
    known = base.isin(["feasible", "infeasible"]) & removed.isin(["feasible", "infeasible"])
    comparable = base.eq("feasible")
    varies = removed[comparable].nunique() > 1
    if not known.all():
        state = "other_held"
    elif not comparable.all():
        state = "baseline_scope_differs"
    elif removed.eq("infeasible").all():
        state = "all17_base_feasible_removed_infeasible"
    elif removed.eq("feasible").all():
        state = "all17_base_feasible_removed_feasible"
    else:
        state = "removed_varies_among_baseline_feasible"
    return {
        "consistency_state": state,
        "baseline_feasible_conditions": int(comparable.sum()),
        "removed_feasible_comparable_conditions": int((comparable & removed.eq("feasible")).sum()),
        "removed_infeasible_comparable_conditions": int(
            (comparable & removed.eq("infeasible")).sum()
        ),
        "removed_varies_among_comparable": bool(varies),
        "all17_baseline_infeasible": bool(base.eq("infeasible").all()),
    }


def select_constraints(names, coefficients, lower, upper, released):
    """上下包絡を同時解除。辺・節点・係数の接続自体は維持。"""
    if len(names) != len(set(names)) or not set(released) <= set(names):
        raise ValueError("解除対象制約が重複/未定義")
    keep = np.array([name not in released for name in names])
    return np.asarray(coefficients)[keep], np.asarray(lower)[keep], np.asarray(upper)[keep]


def diagnostic_slack(net, upper, incidence, coefficients, lower, ceiling):
    """既存と同じL1過不足。供給・需要を補完せず、所在地は一意な原因ではない。"""
    net, upper = np.asarray(net, float), np.asarray(upper, float)
    if not np.isfinite(np.r_[net, upper]).all() or (upper < 0).any():
        raise ValueError("未知/負上限を過不足へ混ぜない")
    n, m = incidence.shape
    c = np.asarray(coefficients, float)
    matrix = np.column_stack([np.eye(n), incidence, np.eye(n), -np.eye(n)])
    ub = np.column_stack([np.zeros((len(lower), n)), c, np.zeros((len(lower), 2 * n))])
    aub, bub = np.vstack([ub, -ub]), np.r_[ceiling, -np.asarray(lower)]
    answer = linprog(
        np.r_[np.zeros(n + m), np.ones(2 * n)],
        A_ub=aub,
        b_ub=bub,
        A_eq=matrix,
        b_eq=net,
        bounds=[(0, u) for u in upper] + [(None, None)] * m + [(0, None)] * (2 * n),
        method="highs",
    )
    if not answer.success:
        raise ValueError("診断用過不足LP失敗")
    error = max(
        abs(matrix @ answer.x - net).max(),
        max(0, (aub @ answer.x - bub).max()),
        max(0, (-answer.x[:n]).max()),
        max(0, (answer.x[:n] - upper).max()),
        max(0, (-answer.x[n + m :]).max()),
    )
    if error > 1e-6:
        raise ValueError("過不足診断残差超過")
    return {
        "diagnostic_deficit_mw": answer.x[n + m : n + m + n].tolist(),
        "diagnostic_surplus_mw": answer.x[n + m + n :].tolist(),
        "diagnostic_slack_sum_mw": float(answer.fun),
        "diagnostic_residual_mw": float(error),
    }


def pair_simultaneous_conditions(sim):
    """停止8組×8法人×両送電。全法人の比較可能性も前後に残す。"""
    keys = ["delivery_date", "period", "entity_code", "network_scenario"]
    result = []
    for name in sorted(sim.supply_trial_scenario.unique()):
        if name == "reference_no_deduction" or name.endswith("__record_held_reference"):
            continue
        before = sim[sim.supply_trial_scenario.eq(name)]
        after = sim[sim.supply_trial_scenario.eq(name + "__record_held_reference")]
        pair = before.merge(
            after,
            on=keys,
            how="outer",
            validate="one_to_one",
            suffixes=("_before", "_after"),
            indicator=True,
        )
        if not pair._merge.eq("both").all():
            raise ValueError("停止条件対の時刻不一致")
        pair["original_scenario"] = name
        pair["both_baseline_feasible"] = pair.all_firms_status_before.eq(
            "feasible"
        ) & pair.all_firms_status_after.eq("feasible")
        pair["removed_failure_to_feasible_comparable"] = (
            pair.both_baseline_feasible
            & pair.removed_simultaneous_status_before.eq("infeasible")
            & pair.removed_simultaneous_status_after.eq("feasible")
        )
        pair["baseline_became_feasible"] = pair.all_firms_status_before.eq(
            "infeasible"
        ) & pair.all_firms_status_after.eq("feasible")
        result.append(pair)
    return pd.concat(result, ignore_index=True)


def decompose_supply_scope(ledger):
    """停止条件の上限低下を、数値反映後の差と記録保留の集合差へ分ける。"""
    key = ["area", "delivery_date", "period", "entity_code"]
    ref = ledger[ledger.supply_trial_scenario.eq("reference_no_deduction")][
        key + ["remaining_upper_mw"]
    ].rename(columns={"remaining_upper_mw": "no_deduction_remaining_mw"})
    original = ledger[
        ~ledger.supply_trial_scenario.eq("reference_no_deduction")
        & ~ledger.supply_trial_scenario.str.endswith("__record_held_reference")
    ].copy()
    restored = ledger[ledger.supply_trial_scenario.str.endswith("__record_held_reference")][
        ["supply_trial_scenario"] + key + ["remaining_upper_mw"]
    ].copy()
    restored["supply_trial_scenario"] = restored.supply_trial_scenario.str.removesuffix(
        "__record_held_reference"
    )
    restored = restored.rename(columns={"remaining_upper_mw": "record_restored_remaining_mw"})
    result = original.merge(ref, on=key, how="left", validate="many_to_one", indicator=True)
    if not result._merge.eq("both").all():
        raise ValueError("控除なしとの供給範囲照合不一致")
    result = result.drop(columns="_merge").merge(
        restored,
        on=["supply_trial_scenario"] + key,
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    if not result._merge.eq("both").all():
        raise ValueError("記録保留復帰条件との範囲照合不一致")
    result["known_numeric_scenario_reduction_other_mw"] = (
        result.no_deduction_remaining_mw - result.record_restored_remaining_mw
    )
    result["total_reduction_other_mw"] = (
        result.no_deduction_remaining_mw - result.remaining_upper_mw
    )
    if not result.total_reduction_other_mw.eq(
        result.known_numeric_scenario_reduction_other_mw + result.other_reference_return_mw
    ).all():
        raise ValueError("他者上限の数値反映分と記録保留復帰分の分解不一致")
    if not result.known_numeric_scenario_reduction_other_mw.ge(0).all():
        raise ValueError("数値停止反映で上限が増加")
    return result.drop(columns="_merge")


def representative_boundary_checks(accounts, constraints):
    """既存の4代表例の境界会計。地域評価を統合せず必要条件を点検する。"""
    rows = []
    for code, area in [
        ("hokkaido", "北海道"),
        ("hokuriku", "北陸"),
        ("kyushu", "九州"),
        ("jera", None),
    ]:
        x = accounts[accounts.entity_code.eq(code)]
        x = x[x.area.isin(["東京", "中部"])] if area is None else x[x.area.eq(area)]
        if x.empty:
            continue
        c = constraints[constraints.entity_code.eq(code)].set_index("constraint_name")
        if code == "hokkaido":
            bound = -c.loc["北海道・本州間電力連系設備", "lower_mw"]
            formula = "-lower(北本)"
        elif code == "hokuriku":
            bound = c.loc["北陸フェンス", "upper_mw"]
            formula = "upper(南福光−越前)"
        elif code == "kyushu":
            bound = c.loc["関門連系線", "upper_mw"]
            formula = "upper(関門)"
        else:
            bound = (
                c.loc["相馬双葉幹線", "upper_mw"]
                - c.loc["三重東近江線", "lower_mw"]
                - c.loc["南福光連系所・南福光変電所の連系設備", "lower_mw"]
            )
            formula = "upper(相馬)−lower(三重)−lower(南福光);FCは内側で相殺"
        rows.append(
            dict(
                entity_code=code,
                delivery_date=x.delivery_date.iloc[0],
                period=int(x.period.iloc[0]),
                area_scope=area or "東京・中部の外側の境界会計（評価地域は別）",
                net_demand_mw=x.net_demand_mw.sum(),
                remaining_upper_mw=x.remaining_upper_mw.sum(),
                boundary_upper_mw=bound,
                boundary_formula=formula,
                diagnostic_gap_lower_bound_mw=x.net_demand_mw.sum()
                - x.remaining_upper_mw.sum()
                - bound,
            )
        )
    return pd.DataFrame(rows)
