"""JEPX spotの元列・原単位を保持するParquet ETL。"""

import csv
import io
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.raw_acquisition import detect_encoding
from src.time_normalization import TimeAudit, fiscal_dates, parse_date, sha256, time_key

NUMERIC_COLUMNS = {
    "system_price": "システムプライス(円/kWh)",
    **{
        f"area_price_{english}": f"エリアプライス{japanese}(円/kWh)"
        for english, japanese in (
            ("hokkaido", "北海道"),
            ("tohoku", "東北"),
            ("tokyo", "東京"),
            ("chubu", "中部"),
            ("hokuriku", "北陸"),
            ("kansai", "関西"),
            ("chugoku", "中国"),
            ("shikoku", "四国"),
            ("kyushu", "九州"),
        )
    },
    "contracted_volume": "約定総量(kWh)",
    "sell_bid_volume": "売り入札量(kWh)",
    "buy_bid_volume": "買い入札量(kWh)",
    "sell_block_bid_volume": "売りブロック入札総量(kWh)",
    "sell_block_contracted_volume": "売りブロック約定総量(kWh)",
    "buy_block_bid_volume": "買いブロック入札総量(kWh)",
    "buy_block_contracted_volume": "買いブロック約定総量(kWh)",
}


def choose_canonical(records: list[dict], year: int) -> tuple[dict, list[dict]]:
    days = fiscal_dates(year)
    candidates = [
        r
        for r in records
        if r["source_id"] == "jepx_spot"
        and r["status"] in ("downloaded", "imported")
        and r.get("period_start") == str(days[0])
        and r.get("period_end") == str(days[-1])
    ]
    official = [r for r in candidates if r.get("variant") == "spot" and r["status"] == "downloaded"]
    if len(official) != 1 or len({r["sha256"] for r in candidates}) != 1:
        raise ValueError("年度canonical候補が一意でない/異なるSHA。採用判断を確認する")
    return official[0], [r for r in candidates if r is not official[0]]


def parse_quantity(value: str) -> Decimal | None:
    if value == "":
        return None
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        raise ValueError(f"未定義の数値表記: {value!r}")
    return Decimal(value)


def transform(data: bytes, source_sha: str, year: int) -> tuple[pa.Table | None, dict]:
    text, encoding = detect_encoding(data)
    reader = csv.reader(io.StringIO(text, newline=""))
    headers = next(reader)
    if len(set(headers)) != len(headers):
        raise ValueError("元列名重複")
    required = {"受渡日", "時刻コード", *list(NUMERIC_COLUMNS.values())[:13]}
    if missing := required - set(headers):
        raise ValueError(f"必須元列がない: {sorted(missing)}")
    aliases = {k: v for k, v in NUMERIC_COLUMNS.items() if v in headers}
    if set(headers) & (
        set(aliases)
        | {
            "delivery_date",
            "period",
            "timestamp_start",
            "timestamp_end",
            "timezone",
            "source_snapshot_id",
            "source_row_number",
        }
    ):
        raise ValueError("正規化列と元列の名前衝突")
    raw = {name: [] for name in headers}
    quantities = {name: [] for name in aliases}
    dates, periods, starts, ends, row_numbers = [], [], [], [], []
    audit = TimeAudit(fiscal_dates(year))
    failures, missing_values = [], []
    source_keys = []
    for number, cells in enumerate(reader, 2):
        if len(cells) != len(headers):
            raise ValueError(f"元CSV列数不一致: 行{number}")
        row = dict(zip(headers, cells, strict=True))
        for name in headers:
            raw[name].append(row[name])
        day = parse_date(row["受渡日"])
        if not re.fullmatch(r"[1-9]\d?", row["時刻コード"]):
            raise ValueError(f"不正な時刻コード: 行{number}")
        period = int(row["時刻コード"])
        key = time_key(day, period)
        audit.observe(day, (), (period,), "30min")
        source_keys.append((day, period))
        dates.append(day)
        periods.append(period)
        starts.append(datetime.fromisoformat(key[2]))
        ends.append(datetime.fromisoformat(key[3]))
        row_numbers.append(number)
        for normalized, original in aliases.items():
            value = row[original]
            try:
                parsed = parse_quantity(value)
                quantities[normalized].append(parsed)
                if parsed is None:
                    missing_values.append(
                        {
                            "source_row_number": number,
                            "source_column": original,
                            "original_value": value,
                            "kind": "empty_cell",
                        }
                    )
            except ValueError:
                failures.append(
                    {
                        "source_row_number": number,
                        "source_column": original,
                        "original_value": value,
                    }
                )
                quantities[normalized].append(None)
    qa = audit.result()
    report = {
        "time_qa": qa,
        "encoding_detection": encoding,
        "original_columns": headers,
        "column_mapping": aliases,
        "numeric_parse_failures": failures,
        "numeric_parse_failure_count": len(failures),
        "missing_values": missing_values,
        "missing_policy": "空セルのみ明示null。非数値は列挙して出力停止。補完/除外しない",
        "column_statistics": {},
        "numeric_schema": {},
        "outlier_policy": "異常値を含め全行保持。削除/winsorize/経済派生なし",
        "all_passed": qa["passed"] and not failures,
    }
    for name, values in quantities.items():
        series = pd.Series([float(v) if v is not None else None for v in values], dtype="float64")
        report["column_statistics"][name] = {
            "missing_count": int(series.isna().sum()),
            "missing_rate": float(series.isna().mean()),
            "summary": {
                k: (None if pd.isna(v) else float(v)) for k, v in series.describe().items()
            },
            "unit": "JPY/kWh" if "price" in name else "kWh",
        }
    if not report["all_passed"]:
        return None, report
    arrays = {name: pa.array(values, type=pa.string()) for name, values in raw.items()}
    arrays.update(
        {
            "delivery_date": pa.array(dates, type=pa.date32()),
            "period": pa.array(periods, type=pa.int8()),
            "timestamp_start": pa.array(starts, type=pa.timestamp("us", tz="Asia/Tokyo")),
            "timestamp_end": pa.array(ends, type=pa.timestamp("us", tz="Asia/Tokyo")),
            "timezone": pa.array(["Asia/Tokyo"] * len(dates)),
            "source_snapshot_id": pa.array([source_sha] * len(dates)),
            "source_row_number": pa.array(row_numbers, type=pa.int32()),
        }
    )
    for name, values in quantities.items():
        scale = max(
            [2 if "price" in name else 0]
            + [max(0, -v.as_tuple().exponent) for v in values if v is not None]
        )
        if scale > 38:
            raise ValueError("Decimal精度38を超える元数値。丸めず停止")
        arrays[name] = pa.array(values, type=pa.decimal128(38, scale))
        report["numeric_schema"][name] = str(arrays[name].type)
    table = pa.table(arrays)
    output_keys = list(zip(table["delivery_date"].to_pylist(), table["period"].to_pylist()))
    assert output_keys == source_keys
    report["raw_processed_key_match"] = {
        "raw_count": len(source_keys),
        "processed_count": len(output_keys),
        "matched_count": len(source_keys),
        "match_rate": 1.0,
        "raw_unmatched": 0,
        "processed_unmatched": 0,
    }
    report["summary_precision"] = "QA統計表示はfloat64、processed数値はDecimalで原小数を保持"
    return table, report


def run(root: Path, output: Path, report_path: Path, canonical_path: Path, year=2025) -> dict:
    root = root.resolve()
    records = list(
        map(json.loads, (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines())
    )
    record, copies = choose_canonical(records, year)
    raw_path = (root / record["local_path"]).resolve()
    if not raw_path.is_relative_to(root / "data/raw"):
        raise ValueError("raw領域外")
    output = output.resolve()
    if not output.is_relative_to((root / "data/processed").resolve()) or output.is_relative_to(
        (root / "data/raw").resolve()
    ):
        raise ValueError("出力はdata/processed配下のみ")
    if output.exists():
        raise FileExistsError(output)
    before = sha256(raw_path)
    if before != record["sha256"]:
        raise ValueError("入力manifest SHA不一致")
    canonical = {
        "source_id": "jepx_spot",
        "fiscal_year": year,
        "canonical_local_path": record["local_path"],
        "canonical_sha256": before,
        "source_url": record["source_url"],
        "original_filename": record["original_filename"],
        "downloaded_at": record["downloaded_at"],
        "selection_basis": (
            "通常HTTPの公式年度spotファイル。同SHA手動コピーは保持し入力に重ねない"
        ),
        "noncanonical_copies": [
            {
                "local_path": r["local_path"],
                "sha256": r["sha256"],
                "reason": "identical_sha256_copy_retained",
            }
            for r in copies
        ],
    }
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n")
    table, report = transform(raw_path.read_bytes(), before, year)
    report.update(
        {
            "github_issue": 10,
            "roadmap_id": 6,
            "input": canonical,
            "output_path": str(output),
            "raw_sha256": before,
            "raw_unchanged": sha256(raw_path) == before,
        }
    )
    if table is not None:
        metadata = {
            b"research_units": json.dumps(
                {n: s["unit"] for n, s in report["column_statistics"].items()}
            ).encode(),
            b"source_sha256": before.encode(),
            b"timezone": b"Asia/Tokyo",
        }
        table = table.replace_schema_metadata(metadata)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            pq.write_table(table, stream, compression="zstd")
        saved = pq.read_table(output)
        assert saved.equals(table, check_metadata=True)
        report["processed_sha256"] = sha256(output)
        report["processed_rows"] = saved.num_rows
        report["processed_columns"] = saved.column_names
        report["parquet_roundtrip_verified"] = True
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not report["all_passed"] or not report["raw_unchanged"]:
        raise ValueError("QA不合格。原表記を報告し、研究判断まで出力を停止")
    return report
