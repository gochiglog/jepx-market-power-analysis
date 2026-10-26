"""公開サンプルの検査。予測断面・FIT範囲を数値の存在から推定しない。"""

import csv
import io
from decimal import Decimal, InvalidOperation

import pandas as pd


def decode_csv(raw):
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return list(csv.reader(io.StringIO(raw.decode(encoding)))), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("対応する文字コードで厳密復号できない")


def inspect_half_hours(frame, timestamps, numeric_columns):
    """元文字列を保持。欠損と非数値を区別し、不完全日を黙って落とさない。"""
    result = frame.copy()
    time = pd.Series(pd.to_datetime(timestamps, errors="raise"), index=frame.index)
    if time.dt.tz is None:
        time = time.dt.tz_localize("Asia/Tokyo")
    if str(time.dt.tz) != "Asia/Tokyo":
        raise ValueError("Asia/Tokyo以外の時刻")
    if not (time.dt.minute.isin([0, 30]) & time.dt.second.eq(0)).all():
        raise ValueError("30分境界以外")
    result["sample_timestamp_start"] = time
    result["sample_timestamp_end"] = time + pd.Timedelta(minutes=30)
    result["sample_delivery_date"] = time.dt.date
    result["sample_period"] = time.dt.hour * 2 + time.dt.minute // 30 + 1
    duplicate_count = int(result.duplicated(["sample_delivery_date", "sample_period"]).sum())
    groups = result.groupby("sample_delivery_date")["sample_period"]
    incomplete = [str(d) for d, g in groups if sorted(g) != list(range(1, 49))]
    issues, missing, summary = [], {}, {}
    for column in numeric_columns:
        values = []
        for index, value in frame[column].items():
            raw = str(value).strip()
            if raw == "":
                values.append(None)
                continue
            try:
                number = Decimal(raw)
                if not number.is_finite():
                    raise InvalidOperation
                values.append(number)
            except InvalidOperation:
                values.append(None)
                issues.append({"index": str(index), "column": column, "raw_value": str(value)})
        missing[column] = sum(str(v).strip() == "" for v in frame[column])
        valid = [v for v in values if v is not None]
        summary[column] = {
            "minimum": str(min(valid)) if valid else None,
            "maximum": str(max(valid)) if valid else None,
        }
    return result, {
        "rows": len(frame),
        "days": len(groups),
        "first_start": str(time.min()),
        "last_end": str((time + pd.Timedelta(minutes=30)).max()),
        "duplicate_keys": duplicate_count,
        "incomplete_days": incomplete,
        "missing_numeric_cells": missing,
        "non_numeric_cells": issues,
        "numeric_summary": summary,
    }


def limited_supply_register(assets):
    """未知設備を0にせず別行で残す。Rとの非重複を原燃種で確認できる範囲だけ選ぶ。"""
    if assets["asset_key_candidate"].duplicated().any():
        raise ValueError("設備ラベル重複")
    result = assets.copy()
    eligible_fuels = {"原子力", "水力", "火力（石炭）", "火力（ガス）", "火力（石油）"}
    result["pv_wind_disjoint_by_raw_fuel"] = result["発電方式・燃種"].isin(eligible_fuels)
    result["limited_scope_status"] = "held_fuel_scope_unresolved"
    disjoint = result["pv_wind_disjoint_by_raw_fuel"]
    result.loc[disjoint, "limited_scope_status"] = "held_capacity_or_equipment"
    eligible = disjoint & result["reference_capacity_positive_unique"]
    result.loc[eligible, "limited_scope_status"] = "candidate_known_reference_subset"
    result["unknown_capacity_assumed_zero"] = False
    result["actual_availability_verified"] = False
    return result


def interpret_subset_result(feasible):
    """限定集合の実行可能解は条件付き証明。不成立は地域全体の不足を意味しない。"""
    return (
        "feasible_within_stated_subset_and_assumptions"
        if feasible
        else "subset_infeasible_no_regionwide_shortage_conclusion"
    )
