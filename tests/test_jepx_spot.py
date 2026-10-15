import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.jepx_spot import choose_canonical, parse_quantity, run, transform
from src.raw_acquisition import detect_encoding
from src.time_normalization import sha256


@pytest.mark.parametrize(
    "value,expected",
    [
        ("13.50", Decimal("13.50")),
        ("0.01", Decimal("0.01")),
        ("15558150", Decimal("15558150")),
        ("-1.25", Decimal("-1.25")),
        ("", None),
    ],
)
def test_numeric_values_exact_and_empty_explicit(value, expected):
    assert parse_quantity(value) == expected


@pytest.mark.parametrize("value", ["***", "NaN", "Inf", "1,000", " 13.5", "N/A"])
def test_non_numeric_values_are_not_silently_nan(value):
    with pytest.raises(ValueError):
        parse_quantity(value)


def local_raw():
    checkout = Path(__file__).resolve().parents[1]
    root = checkout.parents[1] if checkout.parent.name == ".worktrees" else checkout
    path = root / "data/raw/jepx/spot/spot_summary_2025.csv"
    if not path.exists():
        pytest.skip("研究rawがない環境")
    return root, path


def test_real_fy2025_parquet_roundtrip_and_keys(tmp_path):
    root, raw = local_raw()
    original = raw.read_bytes()
    records = list(
        map(json.loads, (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines())
    )
    canonical, copies = choose_canonical(records, 2025)
    assert len(copies) == 1 and copies[0]["sha256"] == canonical["sha256"]
    manifest = tmp_path / "metadata/acquisition_manifest.jsonl"
    manifest.parent.mkdir()
    manifest.write_text("\n".join(json.dumps(r) for r in [canonical, *copies]))
    sample = tmp_path / canonical["local_path"]
    sample.parent.mkdir(parents=True)
    sample.write_bytes(original)
    output = tmp_path / "data/processed/jepx/spot/jepx_spot_FY2025.parquet"
    qa = run(tmp_path, output, tmp_path / "qa.json", tmp_path / "canonical.json")
    table = pq.read_table(output)
    assert table.num_rows == 17520
    assert qa["time_qa"]["days"] == 365 and qa["time_qa"]["passed"]
    assert qa["raw_processed_key_match"]["match_rate"] == 1
    assert qa["numeric_parse_failure_count"] == 0 and not qa["missing_values"]
    assert str(table.schema.field("timestamp_start").type) == "timestamp[us, tz=Asia/Tokyo]"
    assert str(table.schema.field("system_price").type) == "decimal128(38, 2)"
    assert str(table.schema.field("contracted_volume").type) == "decimal128(38, 0)"
    assert table["system_price"][0].as_py() == Decimal("13.50")
    assert table["area_price_hokkaido"][0].as_py() == Decimal("15.41")
    assert table["timestamp_end"][-1].as_py().isoformat() == "2026-04-01T00:00:00+09:00"
    assert str(table["delivery_date"][-1].as_py()) == "2026-03-31"
    text, _ = detect_encoding(original)
    rows = list(csv.DictReader(io.StringIO(text)))
    for name in rows[0]:
        assert table[name].to_pylist() == [r[name] for r in rows]
    assert raw.read_bytes() == original and sample.read_bytes() == original
    assert qa["processed_sha256"] == sha256(output)
    assert all(s["missing_rate"] == 0 for s in qa["column_statistics"].values())
    with pytest.raises(FileExistsError):
        run(tmp_path, output, tmp_path / "qa.json", tmp_path / "canonical.json")


def test_parse_failure_reports_raw_value_and_stops_processed():
    _, path = local_raw()
    text, _ = detect_encoding(path.read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    rows[1][rows[0].index("システムプライス(円/kWh)")] = "未確定"
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    table, report = transform(stream.getvalue().encode("utf-8-sig"), "fixture", 2025)
    assert table is None and not report["all_passed"]
    assert report["numeric_parse_failure_count"] == 1
    assert report["numeric_parse_failures"][0]["original_value"] == "未確定"


def test_duplicate_key_and_missing_period_fail_without_dropping():
    _, path = local_raw()
    text, _ = detect_encoding(path.read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    rows[2][1] = rows[1][1]
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    table, report = transform(stream.getvalue().encode("utf-8-sig"), "fixture", 2025)
    assert table is None and report["time_qa"]["rows"] == 17520
    assert report["time_qa"]["duplicate_keys"] == 1
    assert report["time_qa"]["invalid_time_groups"] == 1


def test_conflicting_raw_versions_require_selection_decision():
    base = {
        "source_id": "jepx_spot",
        "status": "downloaded",
        "variant": "spot",
        "period_start": "2025-04-01",
        "period_end": "2026-03-31",
        "sha256": "a",
    }
    conflicting = dict(base, status="imported", variant="manual_spot", sha256="b")
    with pytest.raises(ValueError, match="採用判断"):
        choose_canonical([base, conflicting], 2025)
