"""市場接続のキー、原値、対象範囲差、共通系列・ネットワークの契約。"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.interconnector_market import (
    SYSTEM_MW,
    VARIANTS,
    make_table,
    normalize_rows,
    parse_quantity,
)
from src.market_panel import AREAS, MARKET_KEY, TIME_KEY, checked_join, key_qa

ROOT = (
    Path(__file__).resolve().parents[3]
    if ".worktrees" in str(Path(__file__))
    else Path(__file__).resolve().parents[1]
)
OUTPUT = ROOT / "data/processed/market/20261016_market_diagnostic_v1"


def test_join_preserves_unknown_left_and_reports_unused_right():
    left = pd.DataFrame({"id": [1, 2], "original": ["x", "y"]})
    right = pd.DataFrame({"id": [1, 3], "value": [10, 30]})
    merged, qa, unused = checked_join(left, right, ["id"], label="test", require_complete=False)
    assert merged.id.tolist() == [1, 2]
    assert merged.original.tolist() == ["x", "y"]
    assert pd.isna(merged.value.iloc[1])
    assert qa["unmatched"] == 1 and qa["unused_right_rows"] == 1
    assert unused.id.tolist() == [3]
    with pytest.raises(ValueError, match="未対応"):
        checked_join(left, right, ["id"], label="test")


def test_join_rejects_row_multiplication_and_column_overwrite():
    with pytest.raises(ValueError, match="重複"):
        checked_join(pd.DataFrame({"id": [1]}), pd.DataFrame({"id": [1, 1]}), ["id"], label="x")
    with pytest.raises(ValueError, match="衝突"):
        checked_join(
            pd.DataFrame({"id": [1], "v": [1]}),
            pd.DataFrame({"id": [1], "v": [2]}),
            ["id"],
            label="x",
        )


def test_null_keys_are_not_silently_matched():
    with pytest.raises(ValueError, match="null"):
        key_qa(pd.DataFrame({"id": [None]}), ["id"])


def capacity_row():
    return dict(
        対象断面="翌々日",
        策定日="20260329",
        **{"策定／更新後": "策定"},
        連系線="任意のフェンス",
        年月日="2026/03/31",
        時刻="24:00",
        方向="逆方向",
        **{c: "-237.000" for c in SYSTEM_MW},
        最新更新年月日時刻="2026/03/29 15:00:00",
    )


def test_capacity_signed_mw_originals_and_fiscal_last_period():
    raw = capacity_row()
    record = dict(variant=VARIANTS[0], local_path="raw.csv", sha256="abc")
    rows, mapping, missing = normalize_rows([raw], record, "")
    row = rows[0]
    assert all(row[c] == value for c, value in raw.items())
    assert row["period"] == 48 and row["delivery_date"] == date(2026, 3, 31)
    assert row["timestamp_end"].isoformat() == "2026-04-01T00:00:00+09:00"
    assert row["available_capacity_mw"] == Decimal("-237.000")
    assert row["direction_raw"] == "逆方向" and not missing
    assert row["directional_geography_status"] == "not_assigned_no_import_aggregation"
    t = make_table(rows, mapping)
    assert t["available_capacity_mw"].type.scale == 6
    assert str(t["timestamp_start"].type.tz) == "Asia/Tokyo"


def test_update_and_file_metadata_are_distinct_not_actual_flow():
    raw = dict(
        対象年月日="2025/04/01",
        時刻="00:30",
        連系線名="link",
        **{"順方向予想潮流(MW)": "10", "移動供給力(MW)": "***"},
    )
    row = normalize_rows(
        [raw], dict(variant=VARIANTS[1], local_path="f", sha256="s"), "2026/10/14 12:00 UPDATE"
    )[0][0]
    assert row["row_updated_at"] is None and row["file_latest_updated_at"] is not None
    assert row["qa_update_after_delivery_start"]
    assert "unresolved" in row["flow_semantics"]
    assert row["transfer_supply_mw"] is None
    assert row["qa_numeric_missing_columns"] == ["移動供給力(MW):official_suppressed_marker"]


def test_no_unknown_numeric_coercion_or_sign_clipping():
    assert parse_quantity("-1.234")[0] == Decimal("-1.234")
    assert parse_quantity("") == (None, "blank")
    for value in ("N/A", "NaN", "Infinity", "1.1234567"):
        with pytest.raises(ValueError):
            parse_quantity(value)


@pytest.fixture(scope="module")
def actual_base():
    if not (OUTPUT / "market_base_FY2025.parquet").exists():
        pytest.skip("実raw由来の市場成果物がない環境")
    return pq.read_table(OUTPUT / "market_base_FY2025.parquet").to_pandas()


def test_actual_market_keys_and_independent_source_cell_preservation(actual_base):
    base = actual_base
    key_qa(base, MARKET_KEY, expected=157680)
    assert len(base.delivery_date.unique()) == 365
    assert base.groupby(["area", "delivery_date"]).period.nunique().eq(48).all()
    assert set(base.area) == set(AREAS)
    spot = pq.read_table(ROOT / "data/processed/jepx/spot/jepx_spot_FY2025.parquet").to_pandas()
    sens = pq.read_table(
        ROOT / "data/processed/jepx/price_sensitivity/jepx_price_sensitivity_FY2025.parquet"
    ).to_pandas()
    for area, suffix in AREAS.items():
        f = base[base.area.eq(area)].sort_values(TIME_KEY).reset_index(drop=True)
        s = spot.sort_values(TIME_KEY).reset_index(drop=True)
        v = sens.sort_values(TIME_KEY).reset_index(drop=True)
        pd.testing.assert_frame_equal(f[s.columns], s)
        assert f.area_price.tolist() == s["area_price_" + suffix].tolist()
        assert f.system_price_market.tolist() == s.system_price.tolist()
        assert (~f.baseline_price_match_flag).sum() == 2271
        for col in v:
            target = "sensitivity_source__" + col if col in spot and col not in TIME_KEY else col
            assert f[target].tolist() == v[col].tolist()
    assert (~base.baseline_price_match_flag).sum() == 20439


def test_actual_four_profiles_preserve_base_and_a_values(actual_base):
    a = (
        ROOT
        / "data/interim/occto/public_record_annotation/20261016_diagnostic_A_v1"
        / "market_join_by_snapshot_scenario.parquet"
    )
    for snapshot in ("hjks_outage_58852", "hjks_outage_7828"):
        for endpoint in ("early", "late"):
            f = pq.read_table(OUTPUT / f"market_{snapshot}_{endpoint}_FY2025.parquet").to_pandas()
            key_qa(f, MARKET_KEY, expected=157680)
            pd.testing.assert_frame_equal(f[actual_base.columns], actual_base)
            original = pq.read_table(
                a, filters=[("snapshot", "=", snapshot), ("scenario", "=", endpoint)]
            ).to_pandas()
            assert original[original.area.eq("沖縄")].shape[0] == 17520
            expected = (
                original[original.area.ne("沖縄")].sort_values(MARKET_KEY).reset_index(drop=True)
            )
            f = f.sort_values(MARKET_KEY).reset_index(drop=True)
            for col in original:
                target = col if col in MARKET_KEY else "annotation__" + col
                # pa/pandasの辞書型符号化を除き値・nullを比較。
                pd.testing.assert_series_equal(
                    f[target],
                    expected[col],
                    check_names=False,
                    check_dtype=False,
                    check_categorical=False,
                )


def test_actual_network_contexts_no_missing_or_repeated_original_records(actual_base):
    network = pq.read_table(
        OUTPUT / "interconnectors/network_time_contexts_FY2025.parquet"
    ).to_pandas()
    key_qa(network, TIME_KEY, expected=17520)
    assert set(actual_base.network_context_id) == set(network.network_context_id)
    for variant in VARIANTS:
        records = pq.read_table(
            OUTPUT / "interconnectors" / (variant + "_FY2025.parquet"),
            columns=[
                "source_record_id",
                "delivery_date",
                "period",
                "link_name_raw",
                "direction_raw",
            ],
        )
        f = records.to_pandas()
        key_qa(f, [*TIME_KEY, "link_name_raw", "direction_raw"])
        refs = [r for items in network[variant + "__source_record_ids"] for r in items]
        assert len(refs) == len(set(refs)) == len(f)
        assert set(refs) == set(f.source_record_id)
        assert f.delivery_date.nunique() == 365
        assert (
            f.groupby(["delivery_date", "link_name_raw", "direction_raw"])
            .period.nunique()
            .eq(48)
            .all()
        )


def test_snapshot_capacity_impact_reports_reallocation_not_small_energy_effect(actual_base):
    repo = Path(__file__).resolve().parents[1]
    impact = pd.read_csv(repo / "reports/snapshot_state_capacity_impact_20261016.csv")
    assert impact.changed_cells.sum() == 57186
    assert impact.known_capacity_cells.sum() == 57186
    assert impact.calculable_gap_cells.sum() == 57090
    assert impact.zero_gap_cells.sum() == 54307
    assert impact.known_reference_capacity_kw_cells.sum() == 8706786000
    assert impact.reference_gap_kw_cells.sum() == 8525152248
    assert impact.zero_reference_gap_kw_cells.sum() == 8369644000
    chubu = impact[impact.area.eq("中部")].iloc[0]
    assert chubu.generation_kwh == 0 and chubu.reference_gap_kw_cells == 4928850000
    # 変更なしを未知量0に補完しない。区分からの減少と別区分への増加の純計は0。
    assert impact[impact.area.eq("東京")].reference_gap_kw_cells.isna().all()
    net = pd.read_csv(repo / "reports/snapshot_state_net_reallocation_20261016.csv")
    assert net.groupby("area").cells.sum().eq(0).all()
    for col in (
        "known_reference_capacity_kw_cells",
        "reference_gap_kw_cells",
        "zero_reference_gap_kw_cells",
        "generation_kwh",
    ):
        assert net.groupby("area")[col].sum(min_count=1).fillna(0).eq(0).all()
