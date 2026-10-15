import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from src.time_normalization import (
    CONTRACTS,
    JST,
    TIME_COLUMNS,
    TimeAudit,
    demand_key,
    fiscal_dates,
    normalize_file,
    output_filename,
    parse_date,
    period_from_label,
    select_records,
    time_key,
)


@pytest.mark.parametrize(
    "variant,prefix",
    [
        ("reserve_nextday2", "occto_reserve_block_dayahead2"),
        ("reserve_latest", "occto_reserve_block_dayahead_actual"),
        ("reserve_interconnector_nextday2", "occto_reserve_interconnector_dayahead2"),
        ("reserve_interconnector_latest", "occto_reserve_interconnector_dayahead_actual"),
    ],
)
def test_reserve_output_names_identify_kind_horizon_and_period(variant, prefix):
    record = {
        "variant": variant,
        "local_path": "data/raw/04_download.csv",
        "period_start": "2025-04-01",
        "period_end": "2025-04-30",
    }
    assert output_filename(record) == f"{prefix}_2025-04-01_2025-04-30.csv"
    assert record["local_path"] == "data/raw/04_download.csv"


@pytest.mark.parametrize(
    "day",
    [
        date(2025, 1, 31),
        date(2025, 12, 31),
        date(2025, 3, 31),
        date(2026, 3, 31),
        date(2024, 2, 29),
        date(2024, 2, 28),
    ],
)
def test_day_boundaries_and_jst(day):
    first, last = time_key(day, 1), time_key(day, 48)
    assert first[0] == last[0] == day.isoformat()
    assert first[2].endswith("T00:00:00+09:00")
    assert first[3].endswith("T00:30:00+09:00")
    assert last[2].endswith("T23:30:00+09:00")
    assert last[3] == f"{day + timedelta(days=1)}T00:00:00+09:00"
    assert first[4] == last[4] == JST.key == "Asia/Tokyo"
    for period in range(1, 49):
        key = time_key(day, period)
        assert datetime.fromisoformat(key[3]) - datetime.fromisoformat(key[2]) == timedelta(
            minutes=30
        )


@pytest.mark.parametrize(
    "label,reference,expected",
    [
        ("00:00", "start", 1),
        ("00:30", "start", 2),
        ("23:30", "start", 48),
        ("00:30", "end", 1),
        ("23:30", "end", 47),
        ("24:00", "end", 48),
    ],
)
def test_labels_have_explicit_reference(label, reference, expected):
    assert period_from_label(label, reference=reference) == expected


@pytest.mark.parametrize(
    "label,reference",
    [
        ("00:00", "end"),
        ("24:00", "start"),
        ("24:30", "end"),
        ("25:00", "end"),
        ("00:15", "start"),
        ("0:00", "start"),
        ("23:30", "unknown"),
    ],
)
def test_ambiguous_invalid_labels_rejected(label, reference):
    with pytest.raises(ValueError):
        period_from_label(label, reference=reference)


@pytest.mark.parametrize("period", [0, 49, 1.0, True, "1"])
def test_period_strict(period):
    time_key(date(2025, 4, 1), 1)
    with pytest.raises(ValueError):
        time_key(date(2025, 4, 1), period)


def test_calendar_leap_and_fiscal_year():
    assert len(fiscal_dates(2025)) == 365
    assert len(fiscal_dates(2023)) == 366
    assert date(2024, 2, 29) in fiscal_dates(2023)
    assert fiscal_dates(2025)[-1] == date(2026, 3, 31)
    assert parse_date("2026/03/31") == date(2026, 3, 31)
    with pytest.raises(ValueError):
        parse_date("2025/02/29")


def test_missing_duplicate_and_variable_series():
    days = (date(2026, 3, 13), date(2026, 3, 14))
    audit = TimeAudit(days)
    for day in days:
        lines = ["existing"] + (["中部フェンス", "関西フェンス"] if day == days[1] else [])
        for line in lines:
            for p in range(1, 49):
                audit.observe(day, (line,), (p,), "30min")
    assert audit.result()["passed"]
    assert list(audit.result()["daily_observed_series_count"].values()) == [1, 3]
    audit.observe(days[0], ("existing",), (48,), "30min")
    assert audit.result()["duplicate_keys"] == 1
    assert not audit.result()["passed"]
    missing = TimeAudit(days)
    for p in range(1, 48):
        missing.observe(days[0], ("existing",), (p,), "30min")
    assert missing.result()["invalid_group_examples"][0]["missing_slots"] == [48]
    assert missing.result()["missing_dates"] == [str(days[1])]


def fixture_record(tmp_path, variant, rows):
    path = tmp_path / "data/raw/sample.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows(rows)
    record = {
        "variant": variant,
        "source_id": "fixture",
        "local_path": "data/raw/sample.csv",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "original_filename": "sample.csv",
        "period_start": "2025-04-01",
        "period_end": "2025-04-01",
    }
    return record, path


@pytest.mark.parametrize("variant", list(CONTRACTS))
def test_each_adapter_preserves_cells_and_raw(tmp_path, variant):
    contract = CONTRACTS[variant]
    columns = [contract.date_column, *contract.group_columns]
    if contract.time_column:
        columns.append(contract.time_column)
    columns.append("value")
    preamble = [["2025/03/30 18:19 UPDATE"]] if contract.preamble_rows else []
    if contract.reference == "wide_end":
        labels = [f"{p // 2:02}:{(p % 2) * 30:02}[kWh]" for p in range(1, 49)]
        columns += labels
        rows = [["2025/04/01", *("a" for _ in contract.group_columns), "001.00", *(["0"] * 48)]]
    else:
        if contract.reference == "hourly":
            labels = [f"{h:02}:00～{h + 1:02}:00" for h in range(24)]
            labels += ["日最大電力(MW)", "日電力量(MWh)"]
        elif contract.reference == "period":
            labels = list(map(str, range(1, 49)))
        else:
            ticks = range(48) if contract.reference == "start" else range(1, 49)
            labels = [f"{t // 2:02}:{(t % 2) * 30:02}" for t in ticks]
        rows = [["2025/04/01", *("a" for _ in contract.group_columns), t, "001.00"] for t in labels]
    header = [[""] * len(columns), columns] if contract.header_rows == 2 else [columns]
    record, path = fixture_record(tmp_path, variant, preamble + header + rows)
    before = path.read_bytes()
    audit = TimeAudit((date(2025, 4, 1),))
    report = normalize_file(record, tmp_path, tmp_path / "data/interim/run", audit)
    assert audit.result()["passed"]
    assert path.read_bytes() == before
    assert report["raw_unchanged"]
    output = list(
        csv.reader(io.StringIO(Path(report["output_path"]).read_text(encoding="utf-8-sig")))
    )
    offset = len(preamble) + len(header)
    assert [r[: len(columns)] for r in output[offset:]] == rows
    assert output[offset - 1][: len(columns)] == columns
    assert len(output[offset:]) == len(rows)
    if variant == "generation":
        key = output[offset][len(columns) :]
        assert json.loads(key[1]) == list(range(1, 49))
        assert json.loads(key[3])[-1] == "2025-04-02T00:00:00+09:00"
    if variant == "demand":
        assert all(r[len(columns) + 1] == "" for r in output[offset:])
    with pytest.raises(FileExistsError):
        normalize_file(record, tmp_path, tmp_path / "data/interim/run", audit)
    with pytest.raises(ValueError, match="出力先"):
        normalize_file(record, tmp_path, tmp_path / "data/raw/new", audit)


def test_demand_original_granularity():
    day = date(2026, 3, 31)
    key, kind = demand_key(day, "23:00～24:00")
    assert kind == "hourly" and key[1] == ""
    assert key[0] == "2026-03-31" and key[3] == "2026-04-01T00:00:00+09:00"
    assert demand_key(day, "日電力量(MWh)")[0][1:4] == ("", "", "")


def test_unparseable_row_retained_and_reported(tmp_path):
    record, _ = fixture_record(
        tmp_path,
        "spot",
        [
            ["受渡日", "時刻コード", "value"],
            ["2025/04/01", "49", "7"],
        ],
    )
    audit = TimeAudit((date(2025, 4, 1),))
    report = normalize_file(record, tmp_path, tmp_path / "data/interim/run", audit)
    output = list(csv.reader(Path(report["output_path"]).open(encoding="utf-8-sig")))
    assert output[1][:3] == ["2025/04/01", "49", "7"]
    assert output[1][-3] == "parse_error"
    assert report["parse_errors"] == 1 and not audit.result()["passed"]


def test_wide_missing_time_column_rejected(tmp_path):
    record, _ = fixture_record(
        tmp_path,
        "generation",
        [
            ["対象日", "発電所コード", "エリア", "ユニット名", "00:30[kWh]"],
            ["2025/04/01", "001", "東京", "1号", "100"],
        ],
    )
    with pytest.raises(ValueError, match="48時刻列"):
        normalize_file(
            record, tmp_path, tmp_path / "data/interim/run", TimeAudit((date(2025, 4, 1),))
        )


def test_local_jepx_fy2025_17520_and_manifest_selection(tmp_path):
    # 公開rawはGit外。通常CIではskip、実データがある研究環境で必ず検証。
    checkout = Path(__file__).resolve().parents[1]
    data_root = checkout.parents[1] if checkout.parent.name == ".worktrees" else checkout
    manifest = data_root / "metadata/acquisition_manifest.jsonl"
    if not (data_root / "data/raw/jepx/spot/spot_summary_2025.csv").exists():
        pytest.skip("研究rawがない環境")
    records = [json.loads(line) for line in manifest.read_text().splitlines()]
    selected = select_records(records, 2025)
    assert len(selected) == 129
    record = next(r for r in selected if r["variant"] == "spot")
    # outputは対象repoのinterim限定のため、fixtureへ原バイトをコピーしてテストする。
    destination = tmp_path / record["local_path"]
    destination.parent.mkdir(parents=True)
    destination.write_bytes((data_root / record["local_path"]).read_bytes())
    audit = TimeAudit(fiscal_dates(2025))
    report = normalize_file(record, tmp_path, tmp_path / "data/interim/test", audit)
    assert report["rows"] == 365 * 48 == 17520
    assert audit.result()["days"] == 365 and audit.result()["passed"]
    assert TIME_COLUMNS == [
        "delivery_date",
        "period",
        "timestamp_start",
        "timestamp_end",
        "timezone",
    ]
