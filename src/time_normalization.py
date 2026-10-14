"""元セル・元粒度を保持する時間キーの追加。数値ETL・結合は行わない。"""

import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from src.raw_acquisition import detect_encoding

JST = ZoneInfo("Asia/Tokyo")
TIME_COLUMNS = ["delivery_date", "period", "timestamp_start", "timestamp_end", "timezone"]
EXTRA_COLUMNS = ["time_granularity", "source_record_id", "source_row_number"]
RESERVE_OUTPUT_NAMES = {
    "reserve_nextday2": "occto_reserve_block_dayahead2",
    "reserve_latest": "occto_reserve_block_dayahead_actual",
    "reserve_interconnector_nextday2": "occto_reserve_interconnector_dayahead2",
    "reserve_interconnector_latest": "occto_reserve_interconnector_dayahead_actual",
}


def output_filename(record: dict) -> str:
    prefix = RESERVE_OUTPUT_NAMES.get(record["variant"])
    if prefix:
        start = parse_date(record["period_start"]).isoformat()
        end = parse_date(record["period_end"]).isoformat()
        return f"{prefix}_{start}_{end}.csv"
    return Path(record["local_path"]).name


@lru_cache(maxsize=4096)
def parse_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", value):
        raise ValueError(f"日付形式が未対応: {value!r}")
    if "/" in value and "-" in value:
        raise ValueError(f"日付区切り混在: {value!r}")
    return date.fromisoformat(value.replace("/", "-"))


def fiscal_dates(year: int) -> tuple[date, ...]:
    start, end = date(year, 4, 1), date(year + 1, 4, 1)
    return tuple(start + timedelta(days=i) for i in range((end - start).days))


def period_from_label(value: str, *, reference: str) -> int:
    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError(f"時刻形式が未対応: {value!r}")
    hour, minute = map(int, value.split(":"))
    if minute not in (0, 30) or hour > 24 or (hour == 24 and minute):
        raise ValueError(f"30分境界でない: {value!r}")
    ticks = hour * 2 + minute // 30
    if reference == "start" and 0 <= ticks < 48:
        return ticks + 1
    if reference == "end" and 1 <= ticks <= 48:
        return ticks
    raise ValueError(f"{reference}の受渡日帰属が不正/曖昧: {value!r}")


@lru_cache(maxsize=20000, typed=True)
def time_key(delivery: date, period: int) -> tuple[str, int, str, str, str]:
    if type(period) is not int or not 1 <= period <= 48:
        raise ValueError(f"periodは整数1〜48: {period!r}")
    start = datetime.combine(delivery, time(), JST) + timedelta(minutes=30 * (period - 1))
    return (
        delivery.isoformat(),
        period,
        start.isoformat(),
        (start + timedelta(minutes=30)).isoformat(),
        JST.key,
    )


@lru_cache(maxsize=1024)
def wide_time_key(delivery: date) -> tuple:
    keys = [time_key(delivery, p) for p in range(1, 49)]
    return (
        delivery.isoformat(),
        json.dumps(list(range(1, 49))),
        json.dumps([k[2] for k in keys]),
        json.dumps([k[3] for k in keys]),
        JST.key,
    )


def demand_key(delivery: date, label: str) -> tuple[tuple, str]:
    if label in ("日最大電力(MW)", "日電力量(MWh)"):
        return (delivery.isoformat(), "", "", "", JST.key), "daily_summary"
    match = re.fullmatch(r"(\d{2}:00)～(\d{2}:00)", label)
    if not match:
        raise ValueError(f"需要時間帯が未対応: {label!r}")
    begin, end = (int(v[:2]) for v in match.groups())
    if not 0 <= begin < 24 or end != begin + 1:
        raise ValueError(f"需要は1時間区間を要求: {label!r}")
    start = datetime.combine(delivery, time(), JST) + timedelta(hours=begin)
    return (
        delivery.isoformat(),
        "",
        start.isoformat(),
        (start + timedelta(hours=1)).isoformat(),
        JST.key,
    ), "hourly"


@dataclass(frozen=True)
class Contract:
    date_column: str
    time_column: str | None
    reference: str
    group_columns: tuple[str, ...] = ()
    preamble_rows: int = 0
    header_rows: int = 1


# ブロックNoはコマ間で変化するため、48コマのグループはエリア単位。
# 全キーには元行のブロックNo等を含め、グループ内のコマ重複も独立に検査する。
CONTRACTS = {
    "spot": Contract("受渡日", "時刻コード", "period"),
    "sensitivity": Contract("年月日", "時刻コード", "period"),
    "sensitivity_diff": Contract("年月日", "時刻コード", "period"),
    "generation": Contract(
        "対象日",
        None,
        "wide_end",
        ("発電所コード", "エリア", "ユニット名"),
    ),
    "reserve_nextday2": Contract("対象年月日", "時刻", "end", ("エリア名",), 1),
    "reserve_latest": Contract("対象年月日", "時刻", "end", ("エリア名",), 1),
    "reserve_interconnector_nextday2": Contract(
        "対象年月日",
        "時刻",
        "end",
        ("連系線名",),
        1,
    ),
    "reserve_interconnector_latest": Contract("対象年月日", "時刻", "end", ("連系線名",), 1),
    "system_capacity_dayahead2_planned": Contract(
        "年月日",
        "時刻",
        "end",
        ("連系線", "方向", "対象断面", "策定／更新後", "策定日"),
    ),
    "manual_rei_validation_power_data": Contract("日付", "時刻", "start", header_rows=2),
    "demand": Contract("年月日", "時間帯", "hourly"),
}


@dataclass
class TimeAudit:
    """コマ完全性は日×観測された系列に対して検査し、区間数は仮定しない。"""

    expected_dates: tuple[date, ...]
    groups: dict = field(default_factory=lambda: defaultdict(Counter))
    dates: set = field(default_factory=set)
    keys: set = field(default_factory=set)
    duplicate_keys: int = 0
    rows: int = 0
    granularity: Counter = field(default_factory=Counter)
    daily_entities: dict = field(default_factory=lambda: defaultdict(set))
    errors: list = field(default_factory=list)

    def observe(self, delivery: date, group: tuple, slots: tuple, kind: str):
        self.rows += 1
        self.dates.add(delivery)
        self.granularity[kind] += 1
        self.daily_entities[delivery].add(group)
        for slot in slots:
            key = (delivery, group, slot)
            if key in self.keys:
                self.duplicate_keys += 1
            self.keys.add(key)
            self.groups[(delivery, group, kind)][slot] += 1

    def result(self) -> dict:
        bad = []
        for (day, group, kind), counts in self.groups.items():
            expected = set(range(24)) if kind == "hourly" else set(range(1, 49))
            if kind == "wide_30min":
                expected = {"wide_48"}
            if kind == "daily_summary":
                expected = {"日最大電力(MW)", "日電力量(MWh)"}
            if set(counts) != expected or any(v != 1 for v in counts.values()):
                bad.append(
                    {
                        "delivery_date": str(day),
                        "group": group,
                        "kind": kind,
                        "missing_slots": sorted(expected - counts.keys()),
                        "duplicate_slots": [s for s, v in counts.items() if v > 1],
                    }
                )
        missing = sorted(set(self.expected_dates) - self.dates)
        unexpected = sorted(self.dates - set(self.expected_dates))
        return {
            "rows": self.rows,
            "days": len(self.dates),
            "expected_days": len(self.expected_dates),
            "missing_dates": list(map(str, missing)),
            "unexpected_dates": list(map(str, unexpected)),
            "duplicate_keys": self.duplicate_keys,
            "invalid_time_groups": len(bad),
            "invalid_group_examples": bad[:20],
            "granularity": dict(self.granularity),
            "parse_errors": self.errors,
            "wide_validated_intervals": self.granularity["wide_30min"] * 48,
            "daily_observed_series_count": {str(d): len(v) for d, v in self.daily_entities.items()},
            "passed": not (missing or unexpected or self.duplicate_keys or bad or self.errors),
            "scope": "観測された系列の日内コマ・対象日。資産母集団/数値欠損の保証ではない",
        }


def select_records(records: list[dict], year: int) -> list[dict]:
    """manifestの取得条件で選択。コピー/サンプルの混在を防ぎ、行は削除しない。"""
    start, end = fiscal_dates(year)[0], fiscal_dates(year)[-1]
    selected = []
    for record in records:
        variant = record.get("variant")
        if variant not in CONTRACTS:
            continue
        if variant == "manual_rei_validation_power_data":
            eligible = record["status"] == "imported"
        else:
            eligible = record["status"] == "downloaded" and (
                record.get("phase") == "full"
                or variant in ("spot", "sensitivity", "sensitivity_diff")
                or (
                    variant == "system_capacity_dayahead2_planned"
                    and record.get("phase") == "sample"
                    and record.get("period_start") == start.isoformat()
                    and record.get("period_end") == (start + timedelta(days=6)).isoformat()
                )
            )
        if eligible and record.get("period_start") and record.get("period_end"):
            if (
                parse_date(record["period_start"]) >= start
                and parse_date(record["period_end"]) <= end
            ):
                selected.append(record)
    missing = set(CONTRACTS) - {r["variant"] for r in selected}
    if missing:
        raise ValueError(f"選択対象が存在しない: {sorted(missing)}")
    return selected


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_file(record: dict, raw_root: Path, output_root: Path, audit: TimeAudit) -> dict:
    variant = record["variant"]
    contract = CONTRACTS[variant]
    root = raw_root.resolve()
    input_path = (root / record["local_path"]).resolve()
    allowed_raw = (root / "data/raw").resolve()
    if not input_path.is_relative_to(allowed_raw):
        raise ValueError("入力はdata/raw配下のみ")
    output_root = output_root.resolve()
    if output_root.is_relative_to(allowed_raw):
        raise ValueError("出力先はrawを指定できない")
    if not output_root.is_relative_to((root / "data/interim").resolve()):
        raise ValueError("出力先はdata/interim配下のみ")
    output = output_root / variant / output_filename(record)
    if output.exists():
        raise FileExistsError(output)
    if sha256(input_path) != record["sha256"]:
        raise ValueError(f"manifest SHA256不一致: {input_path}")
    decoded, encoding = detect_encoding(input_path.read_bytes())
    reader = csv.reader(io.StringIO(decoded, newline=""))
    preamble = [next(reader) for _ in range(contract.preamble_rows)]
    headers = [next(reader) for _ in range(contract.header_rows)]
    columns = headers[-1]
    new_columns = TIME_COLUMNS + EXTRA_COLUMNS
    if set(new_columns) & set(columns):
        raise ValueError("正規化列が元列と衝突")
    for column in (contract.date_column, *contract.group_columns):
        if columns.count(column) != 1:
            raise ValueError(f"元列が欠落/曖昧: {column}")
    if contract.time_column and columns.count(contract.time_column) != 1:
        raise ValueError(f"元時刻列が欠落/曖昧: {contract.time_column}")
    wide_columns = []
    if contract.reference == "wide_end":
        wide_columns = [c for c in columns if re.fullmatch(r"\d{2}:\d{2}\[kWh\]", c)]
        periods = [period_from_label(c[:5], reference="end") for c in wide_columns]
        if periods != list(range(1, 49)):
            raise ValueError("発電実績の48時刻列が欠落/重複/順序不正")
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, parse_errors = 0, 0
    with output.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows(preamble)
        for header in headers[:-1]:
            writer.writerow(header + ["time_normalization"] * len(new_columns))
        writer.writerow(columns + new_columns)
        indices = {c: columns.index(c) for c in (contract.date_column, *contract.group_columns)}
        time_index = columns.index(contract.time_column) if contract.time_column else None
        for number, row in enumerate(reader, contract.preamble_rows + contract.header_rows + 1):
            rows += 1
            try:
                if len(row) != len(columns):
                    raise ValueError(f"列数不一致: {len(row)} != {len(columns)}")
                day = parse_date(row[indices[contract.date_column]])
                group = tuple(row[indices[c]] for c in contract.group_columns)
                if contract.reference == "wide_end":
                    key, kind, slots = wide_time_key(day), "wide_30min", ("wide_48",)
                elif contract.reference == "hourly":
                    label = row[time_index]
                    key, kind = demand_key(day, label)
                    slots = (int(label[:2]),) if kind == "hourly" else (label,)
                else:
                    value = row[time_index]
                    if contract.reference == "period":
                        if not re.fullmatch(r"[1-9]\d?", value):
                            raise ValueError(f"コマ番号が整数でない: {value!r}")
                        period = int(value)
                    else:
                        period = period_from_label(value, reference=contract.reference)
                    key, kind, slots = time_key(day, period), "30min", (period,)
                audit.observe(day, group, slots, kind)
                writer.writerow(row + list(key) + [kind, record["sha256"], number])
            except ValueError as error:
                parse_errors += 1
                audit.errors.append(
                    {"file": record["local_path"], "row": number, "error": str(error)}
                )
                # 元行は保持。変則列数の行も削除せず、下流採用不可をQAに記録。
                writer.writerow(
                    row + [""] * len(TIME_COLUMNS) + ["parse_error", record["sha256"], number]
                )
    if sha256(input_path) != record["sha256"]:
        raise RuntimeError("処理前後でraw SHA256が変化")
    return {
        "source_id": record["source_id"],
        "variant": variant,
        "input_path": str(input_path),
        "original_filename": record.get("original_filename"),
        "input_sha256": record["sha256"],
        "raw_unchanged": True,
        "output_path": str(output),
        "output_filename": output.name,
        "output_sha256": sha256(output),
        "rows": rows,
        "parse_errors": parse_errors,
        "encoding_detection": encoding,
        "output_encoding": "utf-8-sig",
        "original_headers": headers,
        "preamble": preamble,
        "wide_time_columns": wide_columns,
        "time_reference": contract.reference,
    }
