"""元頻度・計画断面・原値・方向の根拠範囲を守る。"""

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.demand_supply import (
    DEMAND_TYPES,
    OLD_MARKET,
    RESERVE_NUMERIC,
    normalize_demand_row,
    normalize_reserve_row,
)
from src.interconnector_directions import annotate_frame
from src.interconnector_market import VARIANTS
from src.market_panel import AREAS, MARKET_KEY, key_qa
from src.raw_acquisition import detect_encoding
from src.time_normalization import select_records, sha256

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parents[1] if REPO.parent.name == ".worktrees" else REPO
OUTPUT = ROOT / "data/processed/occto/demand_supply_direction/20261016_native_v1"


def demand_raw(label="23:00～24:00", value="1.125"):
    return dict(
        年月日="2026/03/31", 時間帯=label, **{a: value for a in [*AREAS, "沖縄", "１０エリア計"]}
    )


def record(variant="demand"):
    return dict(
        local_path="input.csv",
        sha256="hash",
        downloaded_at="2026-10-16T00:00:00+09:00",
        variant=variant,
    )


def test_hourly_stays_hourly_and_fiscal_end_boundary():
    raw = demand_raw()
    kind, rows = normalize_demand_row(raw, record(), 2)
    assert kind == "hourly" and len(rows) == 11
    r = rows[0]
    assert r["timestamp_end"].isoformat() == "2026-04-01T00:00:00+09:00"
    assert (r["timestamp_end"] - r["timestamp_start"]).total_seconds() == 3600
    assert "period" not in r
    assert r["demand_hourly_csv_mwh"] == Decimal("1.125")
    assert r["qa_hourly_unit_display_conflict"]
    assert all(r[k] == v for k, v in raw.items())
    assert rows[-1]["area_scope"] == "ten_area_total"


@pytest.mark.parametrize(
    ("label", "kind", "unit"),
    [("日電力量(MWh)", "daily_energy", "MWh"), ("日最大電力(MW)", "daily_peak", "MW")],
)
def test_daily_kinds_have_distinct_quantities_without_peak_time_invention(label, kind, unit):
    actual_kind, rows = normalize_demand_row(demand_raw(label), record(), 26)
    assert actual_kind == kind and rows[0]["quantity_unit"] == unit
    assert DEMAND_TYPES[kind][0] in rows[0]
    assert "timestamp_start" not in rows[0] and "hour_of_delivery" not in rows[0]


def test_missing_is_null_unknown_text_and_new_columns_are_not_dropped():
    _, rows = normalize_demand_row(demand_raw(value=""), record(), 2)
    assert rows[0]["demand_hourly_csv_mwh"] is None
    assert rows[0]["source_missing_reason"] == "blank"
    with pytest.raises(ValueError):
        normalize_demand_row(demand_raw(value="?"), record(), 2)
    with pytest.raises(ValueError, match="未知の需要列"):
        normalize_demand_row(dict(demand_raw(), 新列="x"), record(), 2)


def test_reserve_is_plan_and_preamble_is_file_latest_not_first_publication():
    raw = dict(
        対象年月日="2026/03/31",
        時刻="24:00",
        エリア名="東京",
        ブロックNo="1",
        **{k: "1" for k in RESERVE_NUMERIC},
    )
    r = normalize_reserve_row(raw, record("reserve_latest"), 3, "2026/04/01 12:00 UPDATE")
    assert r["period"] == 48 and r["timestamp_end"].date() == date(2026, 4, 1)
    assert not r["reserve_values_are_realized_actuals"]
    assert r["qa_file_update_after_target_start"]
    assert not r["initial_publication_datetime_known"]
    assert r["file_update_header_raw"] == "2026/04/01 12:00 UPDATE"
    assert all(r[k] == v for k, v in raw.items())


def mapping_rows():
    with (REPO / "metadata/research/interconnector_direction_scope_20261016.csv").open() as f:
        return list(csv.DictReader(f))


def test_fence_direction_retains_area_set_without_pretending_single_region():
    f = pd.DataFrame(
        [
            dict(
                link_name_raw="中部フェンス",
                direction_raw="逆方向",
                delivery_date=date(2026, 3, 14),
                period=1,
                available_capacity_mw=Decimal("-4"),
            )
        ]
    )
    result = annotate_frame(f, mapping_rows(), VARIANTS[0])
    assert json.loads(result.sending_area_set_json.iloc[0]) == ["北陸", "関西"]
    assert result.sending_area.iloc[0] is None and result.receiving_area.iloc[0] == "中部"
    assert result.available_capacity_mw.iloc[0] == Decimal("-4")
    assert not result.raw_quantities_sign_changed.iloc[0]


def test_east_west_constraints_remain_direction_hold_without_name_guess():
    f = pd.DataFrame(
        [
            dict(
                link_name_raw="関西-中国（東）",
                direction_raw="both_columns",
                delivery_date=date(2025, 4, 1),
                period=1,
            )
        ]
    )
    a = annotate_frame(f, mapping_rows(), VARIANTS[1])
    assert a.forward__sending_area.iloc[0] is None
    assert a.forward__direction_status.iloc[0].startswith("held")
    assert a.equipment_scope_class.iloc[0] == "contingency_minimum_F1_F2"


def test_direction_unknown_or_duplicate_mapping_cannot_silently_apply():
    f = pd.DataFrame(
        [
            dict(
                link_name_raw="未知",
                direction_raw="順方向",
                delivery_date=date(2025, 4, 1),
                period=1,
            )
        ]
    )
    with pytest.raises(ValueError, match="未登録"):
        annotate_frame(f, mapping_rows(), VARIANTS[0])
    r = mapping_rows()
    with pytest.raises(ValueError, match="重複"):
        annotate_frame(f, r + [r[0]], r[0]["source_variant"])


@pytest.fixture(scope="module")
def actual_output():
    if not (OUTPUT / "occto_demand_hourly_FY2025.parquet").exists():
        pytest.skip("実rawがない環境")
    return OUTPUT


def test_actual_demand_complete_native_frequency_raw_cells_and_expansion(actual_output):
    q = json.loads((REPO / "reports/demand_supply_direction_qa_20261016.json").read_text())
    paths = [actual_output / f"occto_demand_{kind}_FY2025.parquet" for kind in DEMAND_TYPES]
    frames = [pq.read_table(p).to_pandas() for p in paths]
    for kind, f in zip(DEMAND_TYPES, frames, strict=True):
        key = ["area", "delivery_date"] + (["hour_of_delivery"] if kind == "hourly" else [])
        key_qa(f, key, expected=365 * 11 * (24 if kind == "hourly" else 1))
        assert f.delivery_date.nunique() == 365 and "period" not in f
    combined = pd.concat(frames, ignore_index=True)
    records = select_records(
        [
            json.loads(line)
            for line in (REPO / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
        ],
        2025,
    )
    for record in [r for r in records if r["variant"] == "demand"]:
        path = ROOT / record["local_path"]
        assert sha256(path) == record["sha256"]
        text, _ = detect_encoding(path.read_bytes())
        source = list(csv.DictReader(text.splitlines()))
        f = combined[combined.source_file.eq(record["local_path"])]
        assert len(f) == len(source) * 11
        assert f.groupby("source_row").size().eq(11).all()
        assert f.groupby("source_row").area.nunique().eq(11).all()
        back = f.drop_duplicates("source_row").sort_values("source_row")
        assert back[list(source[0])].values.tolist() == [list(r.values()) for r in source]
        for kind, (quantity, _) in DEMAND_TYPES.items():
            for area, group in f[f.time_granularity.eq(kind)].groupby("area"):
                assert group[quantity].tolist() == [Decimal(v) if v else None for v in group[area]]
    assert q["demand"]["source_rows"] == 9490


def test_actual_reserve_market_connections_preserve_fixed_sources(actual_output):
    for old in [
        ROOT / OLD_MARKET / "market_base_FY2025.parquet",
        *sorted((ROOT / OLD_MARKET).glob("market_hjks_outage_*_FY2025.parquet")),
    ]:
        f = pq.read_table(
            actual_output / old.name.replace(".parquet", "_with_reserve.parquet")
        ).to_pandas()
        original = pq.read_table(old).to_pandas()
        key_qa(f, MARKET_KEY, expected=157680)
        pd.testing.assert_frame_equal(f[original.columns], original)
        assert (~f.baseline_price_match_flag).sum() == 20439
        for variant in ["reserve_nextday2", "reserve_latest"]:
            r = pq.read_table(actual_output / f"occto_{variant}_FY2025.parquet").to_pandas()
            key_qa(r, MARKET_KEY, expected=175200)
            assert r[r.area.eq("沖縄")].shape[0] == 17520
            source = r[r.area.ne("沖縄")].sort_values(MARKET_KEY).reset_index(drop=True)
            actual = f.sort_values(MARKET_KEY).reset_index(drop=True)
            for c in r:
                target = c if c in MARKET_KEY else variant + "__" + c
                pd.testing.assert_series_equal(
                    actual[target], source[c], check_names=False, check_dtype=c not in MARKET_KEY
                )


def test_actual_direction_tables_preserve_all_original_columns_and_types(actual_output):
    for variant in VARIANTS:
        before = pq.read_table(
            ROOT / OLD_MARKET / "interconnectors" / (variant + "_FY2025.parquet")
        )
        after = pq.read_table(actual_output / (variant + "_with_directions_FY2025.parquet"))
        assert len(before) == len(after)
        assert before.equals(after.select(before.column_names))
