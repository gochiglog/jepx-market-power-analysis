"""固定供給の会計と保存公表制約の入力。R未知を0にしない。"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import linprog

AREAS = ("北海道", "東北", "東京", "中部", "北陸", "関西", "中国", "四国", "九州")
EDGES = (
    ("北海道・本州間電力連系設備", "北海道", "東北"),
    ("相馬双葉幹線", "東北", "東京"),
    ("周波数変換設備", "東京", "中部"),
    ("三重東近江線", "中部", "関西"),
    ("南福光連系所・南福光変電所の連系設備", "中部", "北陸"),
    ("越前嶺南線", "北陸", "関西"),
    ("西播東岡山線・山崎智頭線", "関西", "中国"),
    ("阿南紀北直流幹線", "関西", "四国"),
    ("本四連系線", "中国", "四国"),
    ("関門連系線", "中国", "九州"),
)
# 列順はEDGES。各名称の順方向を正とする。
FENCE_COEFFICIENTS = {
    "北陸フェンス": (0, 0, 0, 0, 1, -1, 0, 0, 0, 0),
    "中部フェンス": (0, 0, 0, 1, 1, 0, 0, 0, 0, 0),
    "関西フェンス": (0, 0, 0, 1, 0, 1, 0, 0, 0, 0),
}
LEGACY_LAST_END = datetime(2026, 3, 13)  # 切替日の開始。時刻不明なので同日全体は保留。


@dataclass(frozen=True)
class FixedOutputContract:
    same_plan_stage: bool
    grid_boundary_aligned: bool
    self_consumption_not_subtracted_twice: bool
    overlap_resolved: bool
    fixed_asset_ids: frozenset
    adjustable_asset_ids: frozenset

    def problems(self):
        result = []
        for name in (
            "same_plan_stage",
            "grid_boundary_aligned",
            "self_consumption_not_subtracted_twice",
            "overlap_resolved",
        ):
            if not getattr(self, name):
                result.append(name)
        if self.fixed_asset_ids & self.adjustable_asset_ids:
            result.append("fixed_adjustable_asset_overlap")
        return result


def account_balance(demand, fixed, adjustable, net_import, contract):
    """同じMW/地域/30分の二表現を照合。需給成立自体とは区別する。"""
    problems = contract.problems()
    if fixed is None:
        problems.append("missing_fixed_supply")
    quantities = [demand, fixed, adjustable, net_import]
    if any(x is None for x in quantities):
        return {
            "status": "held",
            "reasons": problems or ["missing_quantity"],
            "net_demand_mw": None,
        }
    if not all(np.isfinite(float(x)) for x in quantities):
        raise ValueError("非有限の会計値")
    if problems:
        return {"status": "held", "reasons": problems, "net_demand_mw": None}
    net = demand - fixed
    gross_error = demand - (adjustable + fixed + net_import)
    net_error = net - (adjustable + net_import)
    return {
        "status": "accounting_comparable",
        "net_demand_mw": net,
        "gross_balance_residual_mw": gross_error,
        "net_balance_residual_mw": net_error,
        "expression_difference_mw": gross_error - net_error,
        "ratio_status": "defined_positive_net_demand" if net > 0 else "held_nonpositive_net_demand",
    }


def compile_legacy_constraints(rows, timestamp_end):
    """公表制約の輸送モデル入力。物理潮流/完全履歴の証明ではない。"""
    if timestamp_end.tzinfo is None or str(timestamp_end.tzinfo) != "Asia/Tokyo":
        raise ValueError("対象時刻はAsia/Tokyo")
    if timestamp_end.replace(tzinfo=None) > LEGACY_LAST_END:
        return {"status": "held_cutover_scope", "constraints": []}
    lookup = {}
    for r in rows:
        key = (r["link_name_raw"], r["direction_raw"])
        if key in lookup:
            raise ValueError("制約キー重複")
        lookup[key] = r
    required = [r[0] for r in EDGES] + ["北陸フェンス"]
    constraints = []
    for name in required:
        pair = [lookup.get((name, direction)) for direction in ("順方向", "逆方向")]
        if any(r is None for r in pair):
            return {"status": "held_missing_constraint", "constraints": []}
        forward, reverse = pair
        keys = ["operating_capacity_mw", "margin_mw", "wide_area_adjustment_mw"]
        if any(r.get(k) is None for r in pair for k in keys):
            return {"status": "held_missing_bound", "constraints": []}
        # 正負軸を持つ元公表値から作る保存計画の包絡。絶対値化しない。
        upper = forward[keys[0]] - forward[keys[1]] - forward[keys[2]]
        lower = reverse[keys[0]] - reverse[keys[1]] - reverse[keys[2]]
        if lower > upper:
            return {"status": "held_inconsistent_bound", "constraints": []}
        if name in FENCE_COEFFICIENTS:
            coeff = FENCE_COEFFICIENTS[name]
        else:
            i = [x[0] for x in EDGES].index(name)
            coeff = tuple(int(j == i) for j in range(len(EDGES)))
        constraints.append(
            {
                "name": name,
                "coefficients": coeff,
                "lower_mw": lower,
                "upper_mw": upper,
                "source_ids": [r["source_record_id"] for r in pair],
                "bound_role": "saved_published_envelope_not_verified_physical_capacity",
            }
        )
    # 関係不明の系列を黙って捨てない。切替後の新名称は対象期間保留へ。
    extra = set(k[0] for k in lookup) - set(required)
    if extra:
        return {
            "status": "held_extra_constraint_scope",
            "constraints": [],
            "extra_names": sorted(extra),
        }
    return {"status": "compiled_saved_plan_legacy", "constraints": constraints}


def solve_fixed_output(
    demand,
    fixed,
    adjustable_upper,
    edges,
    constraints,
    *,
    contract,
    network_ready,
    target=None,
    scale_basis="net",
):
    """基準λ=1の会計照合、または純/総需要倍率。実データの未知を補わない。"""
    if set(demand) != set(fixed) or set(demand) != set(adjustable_upper):
        raise ValueError("地域集合不一致")
    if not network_ready:
        return {"status": "held_network", "value": None}
    if contract.problems():
        return {"status": "held_accounting_scope", "value": None}
    nodes = list(demand)
    values = [*demand.values(), *fixed.values(), *adjustable_upper.values()]
    if any(v is None for v in values):
        return {"status": "held_missing_accounting_input", "value": None}
    if not np.isfinite(np.asarray(values, dtype=float)).all():
        raise ValueError("非有限入力")
    if any(v < 0 for v in adjustable_upper.values()) or any(v < 0 for v in fixed.values()):
        return {"status": "held_negative_supply_input", "value": None}
    if scale_basis not in {"net", "gross"}:
        raise ValueError("需要倍率の基準不明")
    n, m = len(nodes), len(edges)
    matrix = np.zeros((n, n + m))
    matrix[:, :n] = np.eye(n)
    for j, (sender, receiver) in enumerate(edges):
        if sender not in nodes or receiver not in nodes or sender == receiver:
            raise ValueError("域外接続を暗黙に切らない")
        matrix[nodes.index(sender), n + j] = -1
        matrix[nodes.index(receiver), n + j] = 1
    rhs = np.array([float(demand[a] - fixed[a]) for a in nodes])
    bounds = [(0, float(adjustable_upper[a])) for a in nodes] + [(None, None)] * m
    if target is not None:
        i = nodes.index(target)
        denominator = demand[target] - fixed[target] if scale_basis == "net" else demand[target]
        if denominator <= 0:
            return {"status": "held_nonpositive_ratio_denominator", "value": None}
        scale = np.zeros(n)
        scale[i] = -float(denominator)
        matrix = np.column_stack([matrix, scale])
        # 総需要倍率ではRを固定し、純需要倍率ではD−Rそのものを拡大する。
        rhs[i] = -float(fixed[target]) if scale_basis == "gross" else 0
        bounds.append((0, None))
    objective = np.zeros(len(bounds))
    if target is not None:
        objective[-1] = -1
    inequality, limits = [], []
    for c in constraints:
        coeff = c["coefficients"]
        if len(coeff) != m:
            raise ValueError("辺係数の長さ不一致")
        if any(v is None for v in [*coeff, c["lower_mw"], c["upper_mw"]]):
            return {"status": "held_missing_constraint", "value": None}
        if not np.isfinite(np.asarray([*coeff, c["lower_mw"], c["upper_mw"]], float)).all():
            raise ValueError("非有限制約")
        if c["lower_mw"] > c["upper_mw"]:
            return {"status": "held_inconsistent_bound", "value": None}
        row = np.zeros(len(bounds))
        row[n : n + m] = coeff
        inequality.extend([row, -row])
        limits.extend([c["upper_mw"], -c["lower_mw"]])
    answer = linprog(
        objective,
        A_ub=np.asarray(inequality) if inequality else None,
        b_ub=np.asarray(limits) if limits else None,
        A_eq=matrix,
        b_eq=rhs,
        bounds=bounds,
        method="highs",
    )
    if not answer.success:
        return {
            "status": "infeasible_supplied_scenario" if answer.status == 2 else "solver_failure",
            "value": None,
        }
    g, flow = answer.x[:n], answer.x[n : n + m]
    imports = matrix[:, n : n + m] @ flow
    effective_d = rhs + np.array([float(fixed[a]) for a in nodes])
    if target is not None:
        i = nodes.index(target)
        effective_d[i] = (
            answer.x[-1] * float(demand[target])
            if scale_basis == "gross"
            else float(fixed[target]) + answer.x[-1] * float(demand[target] - fixed[target])
        )
    gross_residual = effective_d - (g + imports + np.array([float(fixed[a]) for a in nodes]))
    net_residual = effective_d - np.array([float(fixed[a]) for a in nodes]) - g - imports
    return {
        "status": "feasible_supplied_scenario",
        "value": float(answer.x[-1]) if target else None,
        "generation_mw": g.tolist(),
        "flows_mw": flow.tolist(),
        "max_gross_residual_mw": float(np.max(np.abs(gross_residual))),
        "max_net_residual_mw": float(np.max(np.abs(net_residual))),
    }
