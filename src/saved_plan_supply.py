"""保存計画に基づく参照上限の限定シナリオ。実際の利用可能性を認定しない。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from scipy.optimize import linprog

AREAS = ["北海道", "東北", "東京", "中部", "北陸", "関西", "中国", "四国", "九州", "沖縄"]
HORIZON = 365 * 48
STRATEGIES = ("full_stop_zero", "mean_reduction")
PROFILES = ("58852_early", "58852_late", "7828_early", "7828_late")
STATUS = [
    "no_additional_deduction_record_absent",
    "stop_upper_zero_under_scenario",
    "subtract_published_mean_under_scenario",
    "outside_interval_under_endpoint_scenario",
    "held_invalid_dates",
    "held_unknown_recovery",
    "held_update_candidates",
    "held_multiple_contents",
    "held_unknown_record_type",
    "held_missing_mean_reduction",
    "held_invalid_mean_reduction",
    "held_partial_start_cell",
    "held_reference_capacity",
    "negative_adjusted_upper_retained",
    "reference_without_deduction",
    "held_mapping_or_period",
]
CODE = {name: i for i, name in enumerate(STATUS)}
HELD = set(range(4, 13)) | {15}


def decimal_to_micro(column):
    """kW小数6桁を整数micro-kWへ。nullは内部maskで保持する。"""
    valid = pc.is_valid(column).to_numpy(zero_copy_only=False)
    small = pc.cast(column, pa.decimal128(18, 6))
    scaled = pc.multiply(small, pa.scalar(1000000, type=pa.int64()))
    value = pc.fill_null(pc.cast(scaled, pa.int64()), 0).to_numpy(zero_copy_only=False)
    return value, valid


def micro_to_decimal(values, valid=None, *, mw=False):
    values = pa.array(values, type=pa.int64(), mask=None if valid is None else ~valid)
    denominator = 1000000000 if mw else 1000000
    result = pc.divide(
        pc.cast(values, pa.decimal128(22, 0)),
        pa.scalar(Decimal(denominator), type=pa.decimal128(10, 0)),
    )
    return pc.cast(result, pa.decimal128(28, 9 if mw else 6))


def context_policies(contexts, contents, strategy):
    if strategy not in STRATEGIES:
        raise ValueError("未知の停止反映シナリオ")
    count = max(c["context_id"] for c in contexts) + 1
    codes = np.zeros(count, dtype=np.uint8)
    amounts = np.zeros(count, dtype=np.int64)
    partial_slot = np.full(count, -1, dtype=np.int32)
    boundary = np.zeros(count, dtype=bool)
    copy_count = np.zeros(count, dtype=np.int32)
    for c in contexts:
        idx = c["context_id"]
        if idx == 0:
            continue
        boundary[idx] = c["has_date_boundary"]
        copy_count[idx] = c["original_row_count"] - c["distinct_content_count"]
        for flag, status in [
            ("has_invalid_dates", "held_invalid_dates"),
            ("has_unknown_recovery", "held_unknown_recovery"),
            ("has_update_candidates", "held_update_candidates"),
            ("has_multiple_contents", "held_multiple_contents"),
        ]:
            if c[flag]:
                codes[idx] = CODE[status]
                break
        else:
            ids = c["member_content_ids"]
            if len(ids) != 1:
                codes[idx] = CODE["held_multiple_contents"]
                continue
            record = contents[ids[0]]
            try:
                start = datetime.strptime(record["停止日時"], "%Y/%m/%d %H:%M")
                if start.minute % 30:
                    partial_slot[idx] = int((start - datetime(2025, 4, 1)).total_seconds() // 1800)
            except ValueError:
                codes[idx] = CODE["held_invalid_dates"]
                continue
            if not c["full_scenario_content_ids"]:
                codes[idx] = CODE["outside_interval_under_endpoint_scenario"]
                continue
            kind = record["停止区分"]
            if kind in ("計画停止", "計画外停止") and strategy == "full_stop_zero":
                codes[idx] = CODE["stop_upper_zero_under_scenario"]
            elif kind in ("計画停止", "計画外停止", "出力低下"):
                mean = record["published_mean_reduction_kw"]
                if mean is None:
                    codes[idx] = CODE["held_missing_mean_reduction"]
                elif not mean.is_finite() or mean < 0:
                    codes[idx] = CODE["held_invalid_mean_reduction"]
                else:
                    micro = mean * 1000000
                    if micro != micro.to_integral_value():
                        raise ValueError("低下量の精度がmicro-kWを超える")
                    amounts[idx] = int(micro)
                    codes[idx] = CODE["subtract_published_mean_under_scenario"]
            else:
                codes[idx] = CODE["held_unknown_record_type"]
    return codes, amounts, partial_slot, boundary, copy_count


def adjust_upper(capacity, valid, context_ids, slots, policies, *, source_states=None):
    codes, amounts, partial_slot, boundary, copies = policies
    result = capacity.copy()
    state = codes[context_ids].copy()
    partial = (partial_slot[context_ids] == slots) & boundary[context_ids]
    state[partial] = CODE["held_partial_start_cell"]
    if source_states is not None:
        invalid_empty = (context_ids == 0) & (source_states != "no_applicable_record") & valid
        state[invalid_empty] = CODE["held_mapping_or_period"]
    result[state == CODE["stop_upper_zero_under_scenario"]] = 0
    subtract = state == CODE["subtract_published_mean_under_scenario"]
    result[subtract] -= amounts[context_ids[subtract]]
    held = np.isin(state, list(HELD))
    defined = valid & ~held
    negative = defined & (result < 0)
    state[negative] = CODE["negative_adjusted_upper_retained"]
    state[~valid] = CODE["held_reference_capacity"]
    return result, defined, state, boundary[context_ids], copies[context_ids]


@dataclass(frozen=True)
class Edge:
    sending: str
    receiving: str
    forward_mw: float
    reverse_mw: float


@dataclass(frozen=True)
class Fence:
    coefficients: tuple
    lower_mw: float
    upper_mw: float


def remove_reference_group(adjusted_capacities, entities, firm):
    """名参照による仮除去。調整済み上限を除き、無参照を0と認定しない。"""
    values = np.asarray(adjusted_capacities, dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        return {"status": "held_invalid_adjusted_supply", "remaining": None, "removed": None}
    selected = np.asarray(entities, dtype=object) == firm
    if not selected.any():
        return {"status": "held_no_name_reference", "remaining": None, "removed": None}
    remaining = values.copy()
    remaining[selected] = 0
    return {
        "status": "diagnostic_name_reference_only",
        "remaining": remaining,
        "removed": float(values[selected].sum()),
    }


def solve_transport(demand, supply, edges, *, fences=(), network_complete=True, target=None):
    """全法人需給成立を先行確認するLP。入力不明をゼロにしない。"""
    nodes = list(demand)
    if not network_complete:
        return {"status": "held_undefined_network", "value": None}
    if set(supply) != set(nodes) or target is not None and target not in nodes:
        raise ValueError("地域集合が不一致")
    d = np.array(list(demand.values()), dtype=float)
    cap = np.array([np.nan if supply[k] is None else supply[k] for k in nodes], dtype=float)
    if not np.isfinite(d).all() or (d <= 0).any():
        raise ValueError("需要は正の既知MW")
    if not np.isfinite(cap).all():
        return {"status": "held_unknown_supply", "value": None}
    if (cap < 0).any():
        return {"status": "held_negative_supply", "value": None}
    n, m = len(nodes), len(edges)
    incidence = np.zeros((n, m))
    bounds = [(0, v) for v in cap]
    for j, edge in enumerate(edges):
        if (
            edge.sending not in nodes
            or edge.receiving not in nodes
            or edge.sending == edge.receiving
        ):
            raise ValueError("地域外への接続を暗黙に切断しない")
        limits = [edge.forward_mw, edge.reverse_mw]
        if not np.isfinite(limits).all() or min(limits) < 0:
            return {"status": "held_invalid_edge_bound", "value": None}
        incidence[nodes.index(edge.sending), j] = -1
        incidence[nodes.index(edge.receiving), j] = 1
        bounds.append((-edge.reverse_mw, edge.forward_mw))
    eq = np.concatenate([np.eye(n), incidence], axis=1)
    rhs = d.copy()
    if target is not None:
        scale = np.zeros(n)
        scale[nodes.index(target)] = -d[nodes.index(target)]
        eq = np.column_stack([eq, scale])
        rhs[nodes.index(target)] = 0
        bounds.append((0, None))
    objective = np.zeros(len(bounds))
    if target is not None:
        objective[-1] = -1
    ub, limits = [], []
    for fence in fences:
        if len(fence.coefficients) != m:
            raise ValueError("フェンスの辺係数が不一致")
        if not np.isfinite([fence.lower_mw, fence.upper_mw, *fence.coefficients]).all():
            return {"status": "held_undefined_fence", "value": None}
        if fence.lower_mw > fence.upper_mw:
            return {"status": "held_invalid_fence", "value": None}
        row = np.zeros(len(bounds))
        row[n : n + m] = fence.coefficients
        ub.extend([row, -row])
        limits.extend([fence.upper_mw, -fence.lower_mw])
    result = linprog(
        objective,
        A_ub=np.array(ub) if ub else None,
        b_ub=np.array(limits) if limits else None,
        A_eq=eq,
        b_eq=rhs,
        bounds=bounds,
        method="highs",
    )
    if not result.success:
        return {
            "status": "infeasible_all_suppliers_or_other_demand"
            if result.status == 2
            else "solver_failure",
            "value": None,
            "solver_status": result.status,
        }
    return {
        "status": "feasible_reference_scenario",
        "value": float(result.x[-1]) if target is not None else None,
        "generation_mw": result.x[:n].tolist(),
        "flows_mw": result.x[n : n + m].tolist(),
        "max_balance_error_mw": float(np.max(np.abs(eq @ result.x - rhs))),
    }


def evaluate_named_removal(demand, resources, firm, edges, *, fences=(), network_complete=True):
    """全法人の前検査が成立した条件だけで、調整済み名参照供給を仮除去する。"""
    nodes = list(demand)
    capacities = [r["adjusted_upper_mw"] for r in resources]
    if any(v is None or not np.isfinite(v) or v < 0 for v in capacities):
        return {"comparison_status": "held_adjusted_supply", "baseline": None, "removed": None}
    if any(r["area"] not in nodes for r in resources):
        raise ValueError("モデル外資源を黙ってdropしない")
    supply = {
        node: sum(r["adjusted_upper_mw"] for r in resources if r["area"] == node) for node in nodes
    }
    baseline = solve_transport(
        demand, supply, edges, fences=fences, network_complete=network_complete
    )
    if baseline["status"] != "feasible_reference_scenario":
        return {
            "comparison_status": "baseline_not_feasible_or_defined",
            "baseline": baseline,
            "removed": None,
        }
    excluded = remove_reference_group(capacities, [r["entity_reference"] for r in resources], firm)
    if excluded["remaining"] is None:
        return {
            "comparison_status": "held_no_name_reference",
            "baseline": baseline,
            "removed": None,
        }
    after = {
        node: sum(
            v for r, v in zip(resources, excluded["remaining"], strict=True) if r["area"] == node
        )
        for node in nodes
    }
    removed = solve_transport(
        demand, after, edges, fences=fences, network_complete=network_complete
    )
    return {
        "comparison_status": "all_firms_feasible_then_diagnostic_removal",
        "baseline": baseline,
        "removed": removed,
        "removed_adjusted_mw": excluded["removed"],
        "all_regions_removed_consistently": True,
    }
