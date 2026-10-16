"""30分の原市場列を保持する、等時間の1時間価格比較。需要は元頻度のまま。"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from src.market_panel import AREAS, checked_join, key_qa
from src.time_normalization import fiscal_dates

HOUR_KEY = ["area", "delivery_date", "hour_of_delivery"]
PRICES = ["area_price", "system_price_market", "system_price_sensitivity_base"]
JST = ZoneInfo("Asia/Tokyo")


def hourly_grid(year, areas=None):
    rows = []
    for area in AREAS if areas is None else areas:
        for day in fiscal_dates(year):
            for hour in range(24):
                start = datetime.combine(day, time(hour), JST)
                rows.append(
                    dict(
                        area=area,
                        delivery_date=day,
                        hour_of_delivery=hour,
                        timestamp_start=start,
                        timestamp_end=start + timedelta(hours=1),
                        timezone="Asia/Tokyo",
                    )
                )
    return pd.DataFrame(rows)


def price_pair(first, second):
    if any(isinstance(v, Decimal) and not v.is_finite() for v in (first, second)):
        raise ValueError("非有限価格")
    if pd.isna(first) or pd.isna(second):
        return None, None, None, None, None
    if not isinstance(first, Decimal) or not isinstance(second, Decimal):
        raise ValueError("価格は有限のDecimalである必要がある")
    if not first.is_finite() or not second.is_finite():
        raise ValueError("非有限価格")
    difference = second - first
    return (
        (first + second) / Decimal(2),
        min(first, second),
        max(first, second),
        difference,
        abs(difference),
    )


def hourly_comparison(market, demand, *, year=2025, areas=None):
    key_qa(market, ["area", "delivery_date", "period"])
    key_qa(demand, HOUR_KEY)
    if not market.period.isin(range(1, 49)).all():
        raise ValueError("不正な30分コマ")
    source = market.copy()
    source["hour_of_delivery"] = (source.period - 1) // 2
    result = hourly_grid(year, areas)
    qa = {"joins": [], "original_market_columns": list(market)}
    for half in (1, 2):
        side = source[source.period.mod(2).eq(half % 2)].copy()
        rename = {c: f"halfhour_{half}__{c}" for c in side if c not in HOUR_KEY}
        result, stats, unused = checked_join(
            result,
            side.rename(columns=rename),
            HOUR_KEY,
            label=f"halfhour_{half}",
            require_complete=False,
        )
        if len(unused):
            raise ValueError("年度・地域外の市場行。黙って除外しない")
        qa["joins"].append(stats)
        valid = result[f"halfhour_{half}__period"].notna()
        for bound in ("start", "end"):
            offset = timedelta(minutes=30 * (half - 1 + (bound == "end")))
            original = result.loc[valid, f"halfhour_{half}__timestamp_{bound}"]
            expected = result.loc[valid, "timestamp_start"] + offset
            if not original.eq(expected).all():
                raise ValueError("30分元時刻と1時間区間が不一致")
    result["observed_halfhour_count"] = sum(
        result[f"halfhour_{h}__period"].notna().astype(int) for h in (1, 2)
    )
    result["two_halfhours_present"] = result.observed_halfhour_count.eq(2)
    for name in PRICES:
        cols = [f"halfhour_{h}__{name}" for h in (1, 2)]
        values = [price_pair(a, b) for a, b in result[cols].itertuples(index=False, name=None)]
        for i, label in enumerate(
            (
                "time_mean",
                "minimum",
                "maximum",
                "second_minus_first",
                "absolute_halfhour_difference",
            )
        ):
            result[f"{name}__{label}"] = [v[i] for v in values]
        result[f"{name}__calculation_available"] = [v[0] is not None for v in values]
        result[f"{name}__calculation_status"] = [
            "calculated_equal_duration"
            if v[0] is not None
            else ("missing_halfhour" if count < 2 else "missing_price")
            for v, count in zip(values, result.observed_halfhour_count, strict=True)
        ]
    right = demand.rename(columns={c: "demand_source__" + c for c in demand if c not in HOUR_KEY})
    result, stats, unused = checked_join(
        result,
        right,
        HOUR_KEY,
        label="native_hourly_demand",
        require_complete=False,
    )
    qa["joins"].append(stats)
    if not unused.empty and not set(unused.area).issubset({"沖縄", "１０エリア計"}):
        raise ValueError("想定外の未使用需要キー")
    valid = result["demand_source__timestamp_start"].notna()
    for bound in ("start", "end"):
        if (
            not result.loc[valid, "timestamp_" + bound]
            .eq(result.loc[valid, "demand_source__timestamp_" + bound])
            .all()
        ):
            raise ValueError("需要と価格の時間区間不一致")
    result["demand_source_key_matched"] = valid
    qa.update(
        rows=len(result),
        duplicate_keys=0,
        silent_drop=0,
        row_growth=0,
        incomplete_price_hours=int((~result.two_halfhours_present).sum()),
        missing_demand_hours=int((~valid).sum()),
        unused_demand_by_area=unused.groupby("area").size().to_dict(),
    )
    return result, qa, unused
