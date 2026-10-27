"""実績純需要の判断用入力。原値と候補範囲を残し、採用・RSI計算はしない。"""

from decimal import Decimal

import pandas as pd


def numeric_raw(value):
    if value is None or str(value).strip() == "":
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("非有限値")
    return result


def regional_column_indices(regions, variables, area, required):
    """保存名でなく原ヘッダーの地域範囲を使う。地域欠落・重複を拒否する。"""
    starts = [i for i, value in enumerate(regions) if value == area]
    if len(starts) != 1:
        raise ValueError("原ヘッダーの対象地域が一意でない")
    start = starts[0]
    end = next((i for i in range(start + 1, len(regions)) if regions[i]), len(regions))
    result = {}
    for label in required:
        candidates = [i for i in range(start, end) if variables[i] == label]
        if len(candidates) != 1:
            raise ValueError("地域内の元変数が一意でない")
        result[label] = candidates[0]
    return result


def clock_keys(dates, clocks, *, role):
    if role not in {"start", "end"}:
        raise ValueError("時刻役割は明示する")
    starts = []
    for date, clock in zip(dates, clocks, strict=True):
        text = str(date)
        base = pd.to_datetime(
            text, format="%Y%m%d" if text.isdigit() and len(text) == 8 else "%Y/%m/%d"
        )
        pieces = str(clock).split(":")
        hour, minute = int(pieces[0]), int(pieces[1])
        if minute not in (0, 30) or (len(pieces) > 2 and int(pieces[2]) != 0):
            raise ValueError("30分境界以外")
        if hour < 0 or hour > 24 or (hour == 24 and minute != 0):
            raise ValueError("不正時刻")
        if role == "start" and hour == 24:
            raise ValueError("開始24:00は明示契約にない")
        if role == "end" and hour == 0 and minute == 0:
            raise ValueError("終了00:00は受渡日が曖昧")
        value = base + pd.Timedelta(hours=hour, minutes=minute)
        if role == "end":
            value -= pd.Timedelta(minutes=30)
        starts.append(value.tz_localize("Asia/Tokyo"))
    time = pd.Series(starts)
    return pd.DataFrame(
        {
            "delivery_date": time.dt.date,
            "period": time.dt.hour * 2 + time.dt.minute // 30 + 1,
            "timestamp_start": time,
            "timestamp_end": time + pd.Timedelta(minutes=30),
        }
    )


def net_candidate(demand, renewable, nuclear):
    """異なる固定範囲は呼出側の列名で区別。nullを0にしない。"""
    if any(v is None or pd.isna(v) for v in [demand, renewable, nuclear]):
        return None
    return demand - renewable - nuclear


def candidate_roles(register):
    if register["asset_key_candidate"].duplicated().any():
        raise ValueError("資産ラベル重複")
    result = register.copy()
    result["candidate_analysis_role"] = "held"
    selected = result["limited_scope_status"].eq("candidate_known_reference_subset")
    nuclear = result["発電方式・燃種"].eq("原子力")
    result.loc[selected & nuclear, "candidate_analysis_role"] = "fixed_observed_nuclear_candidate"
    result.loc[selected & ~nuclear, "candidate_analysis_role"] = "adjustable_reference_candidate"
    result["technical_inflexibility_verified"] = False
    result["final_role_adopted"] = False
    return result


def removal_balance_rhs(base_net_demand, removed_fixed_output, *, removal_scope):
    """λ=1での必要調整量。全供給除去はN0+R_i、調整側のみならN0。"""
    if removal_scope not in {"adjustable_only", "all_supply"}:
        raise ValueError("企業除去範囲を明示する")
    if base_net_demand is None or pd.isna(base_net_demand):
        return None
    if removal_scope == "adjustable_only":
        return base_net_demand
    if removed_fixed_output is None or pd.isna(removed_fixed_output):
        return None
    return base_net_demand + removed_fixed_output


def hourly_energy_comparison(frame):
    """同区間の電力量比較のみ。30分送電制約を1時間へ置換しない。"""
    source = frame.copy()
    source["hour"] = ((source["period"] - 1) // 2).astype(int)
    result = []
    for (area, day, hour), group in source.groupby(["area", "delivery_date", "hour"]):
        complete = len(group) == 2 and sorted(group.period) == [hour * 2 + 1, hour * 2 + 2]
        row = {
            "area": area,
            "delivery_date": day,
            "hour": hour,
            "half_hours_complete": complete,
            "network_constraints_averaged": False,
            "hourly_lp_ready": False,
        }
        for column in [
            "demand_actual_sample_mw",
            "renewable_actual_sample_mw",
            "nuclear_regional_sample_mw",
        ]:
            known = complete and group[column].notna().all()
            row[column + "_hour_mwh"] = sum(group[column]) / 2 if known else None
        result.append(row)
    return pd.DataFrame(result)
