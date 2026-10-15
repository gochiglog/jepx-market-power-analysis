import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from src.generation_hjks import (
    asset_id,
    attributes,
    datetime_value,
    generation_long,
    hjks_snapshot,
    mapping_candidates,
    normalize_name,
)
from src.time_normalization import sha256


def record_csv(tmp_path, name, rows, source):
    path = tmp_path / f"data/raw/{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {
        "source_id": source,
        "local_path": str(path.relative_to(tmp_path)),
        "sha256": sha256(path),
    }


def base_row():
    return {
        "発電所コード": "001",
        "エリア": "東京",
        "発電所名": " Ａ　発電所 ",
        "ユニット名": "１号機",
    }


@pytest.mark.parametrize(
    "raw,expected", [(" Ａ　 Ｂ－Ｃ ", "A B-C"), ("1−2—3", "1-2-3"), ("タービン", "タービン")]
)
def test_normalization_is_lexical_only(raw, expected):
    assert normalize_name(raw) == expected


def test_generation_original_kwh_null_negative_boundary_and_sha(tmp_path, monkeypatch):
    monkeypatch.setattr("src.generation_hjks.fiscal_dates", lambda y: [date(2026, 3, 31)])
    row = {
        **base_row(),
        "対象日": "2026/03/31",
        **{f"{p // 2:02d}:{p % 2 * 30:02d}[kWh]": "2" for p in range(1, 49)},
        "日量[kWh]": "91",
        "更新日時": "2026/04/01 15:00",
    }
    row["01:00[kWh]"] = ""
    row["01:30[kWh]"] = "-1"
    record = record_csv(tmp_path, "generation", [row], "occto_generation")
    assets, qa = generation_long(tmp_path, [record], tmp_path / "out.parquet")
    table = pq.read_table(tmp_path / "out.parquet")
    assert qa["passed"] and qa["long_rows"] == 48
    assert qa["null_count"] == 1 and qa["negative_count"] == 1
    assert Decimal(qa["raw_generation_sum"]) == 91
    assert qa["raw_total_equals_long"]
    assert table["generation_kwh_30min"][0].as_py() == 2
    assert table["generation_kwh_30min"][1].as_py() is None
    assert table["generation_raw_value"][1].as_py() == ""
    assert table["plant_code"][0].as_py() == "001"
    assert table["発電所名"][0].as_py() == row["発電所名"]
    assert table["timestamp_end"][-1].as_py().isoformat() == "2026-04-01T00:00:00+09:00"
    assert str(table["delivery_date"][-1].as_py()) == "2026-03-31"
    assert sha256(tmp_path / record["local_path"]) == record["sha256"]
    assert len(assets) == 1


def test_non_numeric_is_listed_and_duplicate_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr("src.generation_hjks.fiscal_dates", lambda y: [date(2025, 4, 1)])
    row = {
        **base_row(),
        "対象日": "2025/04/01",
        **{f"{p // 2:02d}:{p % 2 * 30:02d}[kWh]": "***" for p in range(1, 49)},
    }
    record = record_csv(tmp_path, "bad", [row, row], "occto_generation")
    _, qa = generation_long(tmp_path, [record], tmp_path / "out.parquet")
    assert not qa["passed"] and qa["long_rows"] == 96
    assert len(qa["numeric_parse_failures"]) == 96
    assert qa["duplicate_key_count"] == 48


def test_missing_day_is_not_filled(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.generation_hjks.fiscal_dates", lambda y: [date(2025, 4, 1), date(2025, 4, 2)]
    )
    row = {
        **base_row(),
        "対象日": "2025/04/01",
        **{f"{p // 2:02d}:{p % 2 * 30:02d}[kWh]": "0" for p in range(1, 49)},
    }
    record = record_csv(tmp_path, "missing", [row], "occto_generation")
    _, qa = generation_long(tmp_path, [record], tmp_path / "out.parquet")
    assert not qa["passed"] and qa["missing_dates"] == ["2025-04-02"]
    assert qa["long_rows"] == 48


def test_duplicate_master_and_semantic_aliases_remain_unresolved():
    row = base_row()
    asset = {
        **attributes(row),
        "observed_dates": {"2025-04-01"},
        "long_rows": 48,
        "generation_sum": Decimal(10),
        "absolute_generation_sum": Decimal(10),
    }
    master = {**attributes(row), "snapshot_row_id": "x:2"}
    candidates, qa = mapping_candidates(
        {asset_id(row): asset}, [master, dict(master, snapshot_row_id="x:3")]
    )
    assert qa["unit"]["row_match_rate"] == 0 and qa["unit"]["ambiguity_asset_unit_count"] == 1
    assert qa["plant"]["row_match_rate"] == 1
    assert not candidates[0]["final_mapping"]
    master["unit_name_normalized"] = "単独"
    _, qa = mapping_candidates({asset_id(row): asset}, [master])
    assert qa["unit"]["row_match_rate"] == 0


def test_outage_event_dates_do_not_become_actual_recovery_or_interval(tmp_path):
    row = {
        **base_row(),
        "認可出力": "100000",
        "低下量": "-10",
        "停止区分": "出力低下",
        "種別": "燃料制約",
        "停止日時": "2024/04/01 12:00",
        "復旧予定日": "2025/05/01",
        "最終更新日時": "2025/04/01 10:00",
    }
    record = record_csv(tmp_path, "outage", [row, row], "hjks_outage")
    rows, qa = hjks_snapshot(tmp_path, record, tmp_path / "out")
    assert rows[0]["expected_recovery_datetime"] is None
    assert rows[0]["actual_recovery_datetime"] is None
    assert rows[0]["publication_datetime"] is None
    assert rows[0]["reduction_kw"] == -10 and "reduction_mw" not in rows[0]
    assert qa["negative_reduction_count"] == 2
    assert qa["candidate_event_duplicate_groups"] == 1
    assert qa["start_before_fy_recovery_in_or_after_fy"] == 2
    assert len(rows) == 2 and not rows[0]["event_id_verified"]


def test_capacity_effective_date_is_not_assumed_to_be_asset_mapping_validity(tmp_path):
    row = {
        **base_row(),
        "認可出力": "100000",
        "認可出力（変更後）": "90000",
        "適用開始日": "2025/10/01",
        "稼働開始日": "1900/01/01",
        "稼働終了日": "9999/12/31",
        "最終更新日時": "2025/04/01 10:00",
    }
    record = record_csv(tmp_path, "unit", [row], "hjks_unit")
    rows, qa = hjks_snapshot(tmp_path, record, tmp_path / "out")
    assert rows[0]["valid_from"] is None and rows[0]["valid_to"] is None
    assert rows[0]["changed_authorized_capacity_kw"] == 90000
    assert rows[0]["capacity_effective_date"] == date(2025, 10, 1)
    assert qa["operation_end_9999_count"] == 1


def test_plant_code_and_area_have_priority_over_plant_name_variants():
    row = base_row()
    asset = {
        **attributes(row),
        "observed_dates": {"2025-04-01"},
        "long_rows": 48,
        "generation_sum": Decimal(10),
        "absolute_generation_sum": Decimal(10),
    }
    masters = [
        {**attributes(row), "snapshot_row_id": "x:2"},
        {**attributes(dict(row, 発電所名="別表記", ユニット名="２号機")), "snapshot_row_id": "x:3"},
    ]
    _, qa = mapping_candidates({asset_id(row): asset}, masters)
    assert qa["unit"]["row_match_rate"] == 1 and qa["plant"]["row_match_rate"] == 1


def test_date_only_does_not_silently_become_midnight_datetime():
    issues = []
    assert datetime_value("2025/04/01", issues, {"row": 2}) is None
    assert issues[0]["original_value"] == "2025/04/01"


def test_real_long_output_has_fy2025_keys_original_cells_and_no_conversion():
    checkout = Path(__file__).resolve().parents[1]
    root = checkout.parents[1] if checkout.parent.name == ".worktrees" else checkout
    output = root / "data/interim/occto/generation/generation_long_FY2025.parquet"
    qa_path = checkout / "reports/generation_hjks_FY2025_qa.json"
    if not output.exists() or not qa_path.exists():
        pytest.skip("研究raw/診断出力のない環境")
    qa = json.loads(qa_path.read_text())["generation"]
    assert sha256(output) == qa["output_sha256"]
    parquet = pq.ParquetFile(output)
    assert parquet.metadata.num_rows == 8468544
    dates = pq.read_table(output, columns=["delivery_date"])["delivery_date"]
    assert len(pc.unique(dates)) == 365
    assert pc.min(dates).as_py() == date(2025, 4, 1) and pc.max(dates).as_py() == date(2026, 3, 31)
    assert qa["duplicate_key_count"] == 0 and qa["raw_total_equals_long"]
    current = None
    raw = []
    from src.raw_acquisition import detect_encoding

    for i in range(parquet.num_row_groups):
        group = parquet.read_row_group(i)
        for sample in (
            group.slice(0, 1).to_pylist()[0],
            group.slice(group.num_rows - 1, 1).to_pylist()[0],
        ):
            if current != sample["source_file"]:
                current = sample["source_file"]
                import io

                text, _ = detect_encoding((root / current).read_bytes())
                raw = list(csv.DictReader(io.StringIO(text)))
            original = raw[sample["source_row"] - 2]
            value = original[sample["source_period_column"]]
            assert sample["generation_raw_value"] == value
            assert sample["generation_kwh_30min"] == (Decimal(value) if value else None)
            for field in (
                "発電所コード",
                "エリア",
                "発電所名",
                "ユニット名",
                "対象日",
                "日量[kWh]",
                "更新日時",
            ):
                assert sample[field] == original[field]
            assert sample["timestamp_end"] - sample["timestamp_start"] == __import__(
                "datetime"
            ).timedelta(minutes=30)
    for record in qa["inputs"]:
        assert sha256(root / record["path"]) == record["sha256"]
