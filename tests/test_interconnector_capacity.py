"""週間境界・翌々日/策定・両方向・48コマ・重複キーを検証する。"""

from datetime import date

import pytest

from src.interconnector_capacity import validate_capacity, week_ranges
from src.raw_acquisition import date_range


def fixture_csv():
    header = "対象断面,策定／更新後,策定日,連系線,年月日,時刻,方向,空容量\n"
    rows = [
        f"翌々日,策定,20250330,line,2025/04/01,{p // 2:02}:{p % 2 * 30:02},{direction},-1\n"
        for direction in ["順方向", "逆方向"]
        for p in range(1, 49)
    ]
    return (header + "".join(rows)).encode("cp932")


def test_weeks_cover_365_days_once_and_last_day():
    ranges = list(week_ranges())
    days = [d for a, b in ranges for d in date_range(date.fromisoformat(a), date.fromisoformat(b))]
    assert len(ranges) == 53
    assert len(days) == len(set(days)) == 365
    assert ranges[0] == ("2025-04-01", "2025-04-07")
    assert ranges[-1] == ("2026-03-31", "2026-03-31")
    assert ("2025-12-30", "2026-01-05") in ranges


def test_raw_validation_checks_both_directions_without_converting_signed_values():
    data = fixture_csv()
    result = validate_capacity(data, "2025-04-01", "2025-04-01")["capacity_audit"]
    assert result["row_count"] == 96
    assert result["all_groups_have_48_unique_periods"]
    assert result["both_directions_present"]
    assert result["non_numeric_capacity_value_counts"] == {}


@pytest.mark.parametrize("change", ["missing", "duplicate", "direction", "version", "date"])
def test_bad_response_cannot_enter_annual_download(change):
    data = fixture_csv()
    if change == "missing":
        data = data.rsplit(b"\n", 2)[0] + b"\n"
    if change == "duplicate":
        data += data.splitlines()[1] + b"\n"
    if change == "direction":
        data = data.decode("cp932").replace("逆方向", "順方向").encode("cp932")
    if change == "version":
        data = data.decode("cp932").replace("策定,", "更新後,").encode("cp932")
    if change == "date":
        data = data.replace(b"2025/04/01", b"2025/04/02")
    with pytest.raises(ValueError):
        validate_capacity(data, "2025-04-01", "2025-04-01")


def test_planning_date_must_be_two_days_before_delivery():
    data = fixture_csv().replace(b"20250330", b"20250331")
    with pytest.raises(ValueError, match="2日前"):
        validate_capacity(data, "2025-04-01", "2025-04-01")


def test_annual_gate_rejects_missing_manual_comparison(tmp_path):
    from scripts.acquire_capacity import acquire

    with pytest.raises(ValueError, match="手動CSV"):
        acquire(tmp_path, "full")


def test_annual_audit_rejects_cross_file_duplicate_keys(tmp_path):
    import hashlib
    import json

    from scripts.acquire_capacity import VARIANT
    from scripts.audit_capacity import audit_capacity

    data = fixture_csv()
    (tmp_path / "metadata").mkdir()
    records = []
    for name in ["a.csv", "b.csv"]:
        p = tmp_path / "data/raw/capacity" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        records.append(
            {
                "status": "downloaded",
                "variant": VARIANT,
                "local_path": str(p.relative_to(tmp_path)),
                "file_size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "requested_period_start": "2025-04-01",
                "requested_period_end": "2025-04-01",
            }
        )
    (tmp_path / "metadata/acquisition_manifest.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )
    with pytest.raises(ValueError, match="キー重複"):
        audit_capacity(tmp_path)
