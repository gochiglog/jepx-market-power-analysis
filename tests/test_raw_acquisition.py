"""人工データでraw不変性・形式判定・取得ゲート・収録日監査を検証する。"""

import codecs
import hashlib
import io
import json
import zipfile
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from scripts.acquire_raw import month_ranges, sample_verified, verify_existing
from src.raw_acquisition import (
    date_range,
    detect_encoding,
    inspect_bytes,
    inspect_csv,
    record_download,
    save_exclusive,
)


def test_utf8_bom_and_preamble_are_detected():
    data = (
        codecs.BOM_UTF8
        + '2025/03/30 18:00 UPDATE\n"対象年月日","時刻"\n2025/04/01,00:00\n'.encode()
    )
    result = inspect_csv(data, "対象年月日")
    assert result["encoding"] == "utf-8-sig"
    assert result["bom"] == "UTF-8"
    assert result["header_row"] == 2
    assert result["columns"] == ["対象年月日", "時刻"]
    assert result["period_start"] == "2025-04-01"


def test_cp932_is_detected_without_using_another_sources_encoding():
    text = "年月日,単位\n2025/04/01,①\n"
    decoded, result = detect_encoding(text.encode("cp932"))
    assert decoded == text
    assert result["encoding"] == "cp932"
    assert result["bom"] is None


def test_equivalent_encoding_candidates_are_reported_as_ambiguous():
    _, result = detect_encoding("受渡日,時刻コード\n2025/04/01,1\n".encode("shift_jis"))
    assert result["encoding"] == "ambiguous"
    assert set(result["encoding_candidates"]) == {"cp932", "shift_jis"}


@pytest.mark.parametrize("data", [b"", b"<!DOCTYPE html><html>error</html>", b"col\x00,val"])
def test_empty_html_and_invalid_response_are_rejected(data):
    with pytest.raises(ValueError):
        inspect_bytes(data, "example.csv", "text/html")


@pytest.mark.parametrize("delimiter", [",", "\t", ";"])
def test_delimiter_is_detected(delimiter):
    data = f"日付{delimiter}値\n2025/04/01{delimiter}1\n".encode()
    assert inspect_csv(data)["delimiter"] == delimiter


def test_wide_generation_headers_remain_unchanged():
    columns = ["対象日"] + [f"{i // 2:02}:{i % 2 * 30:02}[kWh]" for i in range(1, 49)]
    data = (",".join(columns) + "\n2025/04/01," + ",".join(["0"] * 48) + "\n").encode()
    result = inspect_csv(data, "対象日")
    assert result["columns"] == columns
    assert result["columns"][-1] == "24:00[kWh]"
    assert result["row_count"] == 1


def test_48_period_audit_reports_missing_duplicate_and_invalid_keys():
    data = "受渡日,時刻コード\n" + "".join(f"2025/04/01,{p}\n" for p in range(1, 49))
    complete = inspect_csv(data.encode(), "受渡日", "時刻コード")["period_audit"]
    assert complete["incomplete_dates"] == []
    broken = data.replace("2025/04/01,48\n", "2025/04/01,49\n2025/04/01,1\n")
    result = inspect_csv(broken.encode(), "受渡日", "時刻コード")["period_audit"]
    assert result["incomplete_dates"] == ["2025-04-01"]
    assert result["duplicate_date_period_keys"] == 1
    assert result["invalid_period_rows"] == 1


def test_month_ranges_cover_fiscal_year_once_including_year_boundary():
    ranges = list(month_ranges())
    dates = [
        day for a, b in ranges for day in date_range(date.fromisoformat(a), date.fromisoformat(b))
    ]
    assert len(ranges) == 12
    assert len(dates) == len(set(dates)) == 365
    assert dates[0] == "2025-04-01" and dates[-1] == "2026-03-31"
    assert ("2025-12-01", "2025-12-31") in ranges
    assert ("2026-02-01", "2026-02-28") in ranges
    assert all((date.fromisoformat(b) - date.fromisoformat(a)).days < 31 for a, b in ranges)


def test_atomic_save_never_overwrites_existing_bytes(tmp_path):
    path = tmp_path / "original.csv"
    save_exclusive(path, b"original")
    with pytest.raises(FileExistsError):
        save_exclusive(path, b"changed")
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


def test_symlink_is_not_overwritten(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"unchanged")
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(FileExistsError):
        save_exclusive(link, b"changed")
    assert original.read_bytes() == b"unchanged"


def test_manifest_records_original_bytes_hash_mime_and_missing_day(tmp_path):
    data = codecs.BOM_UTF8 + "受渡日,時刻コード\n2025/04/01,1\n2025/04/03,1\n".encode()
    result = record_download(
        tmp_path,
        "jepx_spot",
        "人工サンプル",
        "https://example.test/file.csv",
        data,
        {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="original.csv"',
        },
        "https://example.test/file.csv",
        "sample.csv",
        "2025-04-01",
        "2025-04-03",
        "受渡日",
        "時刻コード",
        fields={"downloadKey": "private", "_csrf": "private", "selection": "all"},
    )
    assert (tmp_path / result["local_path"]).read_bytes() == data
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["original_filename"] == "original.csv"
    assert result["request_parameters"] == {"selection": "all"}
    assert result["inspection"]["http_mime_type"] == "application/octet-stream"
    assert result["missing_dates"] == ["2025-04-02"]
    assert datetime.fromisoformat(result["downloaded_at"]).utcoffset().total_seconds() == 9 * 3600
    records = (tmp_path / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
    assert json.loads(records[0]) == result
    verify_existing(tmp_path, result)


@pytest.mark.parametrize("filename", ["../evil.csv", "/tmp/evil.csv"])
def test_path_traversal_is_rejected(tmp_path, filename):
    with pytest.raises(ValueError):
        record_download(
            tmp_path,
            "jepx_spot",
            "test",
            "https://example.test/",
            b"a,b\n1,2\n",
            {},
            "https://example.test/",
            filename,
        )
    assert not (tmp_path / "data/raw").exists()


def test_sample_gate_requires_nonempty_correctly_parsed_target_dates():
    assert not sample_verified([], "spot")
    sample = {
        "variant": "spot",
        "phase": "sample",
        "status": "downloaded",
        "inspection": {"row_count": 1, "observed_dates": ["2025-04-01"]},
    }
    assert sample_verified([sample], "spot")
    sample["missing_dates"] = ["2025-04-02"]
    assert not sample_verified([sample], "spot")


def test_zip_inspection_does_not_extract_or_convert_members():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sample.csv", codecs.BOM_UTF8 + "対象年月日,値\n2025/04/01,1\n".encode())
    result = inspect_bytes(buffer.getvalue(), "sample.zip", "application/zip", "対象年月日")
    assert result["format"] == "zip"
    assert result["members"]["sample.csv"]["bom"] == "UTF-8"


def test_xlsx_inspection_records_sheet_names_and_columns():
    workbook = Workbook()
    workbook.active.title = "人工サンプル"
    workbook.active.append(["対象日", "数量"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    result = inspect_bytes(buffer.getvalue(), "sample.xlsx")
    assert result["sheet_names"] == ["人工サンプル"]
    assert result["columns"] == {"人工サンプル": ["対象日", "数量"]}
    assert result["encoding"] == "not_applicable"


def test_modified_existing_file_is_detected(tmp_path):
    path = tmp_path / "data/raw/source/file.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    with pytest.raises(ValueError, match="SHA256"):
        verify_existing(
            tmp_path, {"local_path": str(path.relative_to(tmp_path)), "sha256": "wrong"}
        )


def test_cp932_shift_jis_mapping_difference_is_kept_explicit():
    decoded, inspection = detect_encoding(
        "年月日,時間帯\n2025/04/01,00:00～01:00\n".encode("cp932")
    )
    assert inspection["encoding"] == "ambiguous"
    assert inspection["inspection_decoder"] == "cp932"
    assert "字形差" in inspection["encoding_evidence"]
    assert "～" in decoded


def test_hourly_time_labels_are_not_split_into_30_minute_periods():
    data = "年月日,時間帯\n2025/04/01,00:00～01:00\n".encode("cp932")
    result = inspect_csv(data, "年月日")
    assert result["observed_time_labels"] == ["00:00～01:00"]
    assert result["row_count"] == 1
    assert "period_audit" not in result


def test_sample_with_period_anomalies_cannot_enable_bulk_download():
    sample = {
        "variant": "spot",
        "phase": "sample",
        "status": "downloaded",
        "inspection": {
            "row_count": 48,
            "observed_dates": ["2025-04-01"],
            "period_audit": {"duplicate_date_period_keys": 1},
        },
    }
    assert not sample_verified([sample], "spot")
