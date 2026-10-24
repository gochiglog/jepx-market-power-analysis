"""閲覧層の表示改善が原値・型・監査情報を変えないことを検査する。"""

import csv
import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.data_browser import (
    canonical_name,
    column_catalog,
    japanese_display,
    read_page,
)

ROOT = Path(__file__).resolve().parents[1]


def test_unknown_name_requires_explicit_mapping():
    with pytest.raises(ValueError, match="未定義"):
        canonical_name("勝手な意味を与えない列")


def test_page_preserves_values_types_and_filters(tmp_path):
    data = pa.table(
        {
            "area": ["北海道", "東北", "北海道"],
            "system_price": [12.0, 15.0, 13.0],
            "system_price_sensitivity_base": [11.0, 15.0, 13.0],
        }
    )
    path = tmp_path / "source.parquet"
    pq.write_table(data, path)
    before = path.read_bytes()
    catalog = column_catalog(path, ROOT, "test")
    table = {"source_path": "source.parquet"}
    page, qa = read_page(tmp_path, table, catalog, offset=1, limit=1, filters={"area": "北海道"})
    assert page.column_names == ["area", "system_price_market"]
    assert page["system_price_market"].to_pylist() == [13.0]
    assert page.schema.field("system_price_market").type == pa.float64()
    assert qa["matched_rows"] == 2
    full, _ = read_page(tmp_path, table, catalog, detail=True)
    assert full["system_price_sensitivity_base"].to_pylist() == [11.0, 15.0, 13.0]
    assert path.read_bytes() == before
    empty, qa = read_page(tmp_path, table, catalog, filters={"area": "該当なし"})
    assert len(empty) == qa["matched_rows"] == 0
    with pytest.raises(ValueError, match="条件列"):
        read_page(tmp_path, table, catalog, filters={"invented": "北海道"})


def test_all_catalog_columns_are_unique_and_match_fixed_schema():
    tables = json.loads((ROOT / "metadata/view_table_registry.json").read_text())["tables"]
    with (ROOT / "metadata/view_column_dictionary.csv").open() as f:
        columns = list(csv.DictReader(f))
    assert len(tables) == 34
    for table in tables:
        path = ROOT / table["source_path"]
        if not path.exists():
            pytest.skip("実データはGit管理外")
        registered = [r for r in columns if r["table_id"] == table["table_id"]]
        assert column_catalog(path, ROOT, table["table_id"]) == [
            {**r, "ordinal": int(r["ordinal"])} for r in registered
        ]
        if path.name.startswith("hjks_unit_"):
            valid_to = next(r for r in registered if r["source_column"] == "valid_to")
            assert "未確認なのでnull" in valid_to["definition_ja"]
            assert "規制" not in valid_to["definition_ja"]
        names = [r["standard_english_name"] for r in registered]
        assert len(names) == len(set(names))
        assert all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names)
        assert all(r["japanese_name"] and r["definition_ja"] for r in registered)
        page, qa = read_page(ROOT, table, registered, limit=1)
        assert qa["matched_rows"] == table["rows"]
        assert len(page) == 1
        assert "system_price_sensitivity_base" not in page.column_names
        full, _ = read_page(ROOT, table, registered, limit=1, detail=True)
        source_first = next(pq.ParquetFile(path).iter_batches(batch_size=1))
        assert full.equals(pa.Table.from_batches([source_first]).rename_columns(names))
        display = japanese_display(page, registered)
        assert len(display) == 1


def test_network_example_keeps_signed_numbers_and_japanese_display():
    tables = json.loads((ROOT / "metadata/view_table_registry.json").read_text())["tables"]
    table = next(t for t in tables if t["table_id"] == "t13")
    path = ROOT / table["source_path"]
    if not path.exists():
        pytest.skip("実データはGit管理外")
    catalog = column_catalog(path, ROOT, "t13")
    page, _ = read_page(ROOT, table, catalog, limit=2)
    assert page["available_capacity_mw"].to_pylist() == [680, -237]
    shown = japanese_display(page, catalog)
    assert "北海道" in shown.iloc[0].to_list()
    assert "公式資料で方向確認済み" in shown.iloc[0].to_list()
    assert page["direction_status"][0].as_py() == "official_orientation_verified"
