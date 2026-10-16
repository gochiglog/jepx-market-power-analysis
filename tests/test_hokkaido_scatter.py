"""北海道の参照群・欠測・方向と、記述図の元データを検査する。"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.hokkaido_scatter import (
    A_PATH,
    KEY,
    add_unit_reference,
    aggregate_reference,
    classify_assets,
    nullable_sum,
    select_import,
    tied_quartiles,
)

D = Decimal
REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parents[1] if REPO.parent.name == ".worktrees" else REPO
OUTPUT = ROOT / "data/processed/description/hokkaido/20261016_scatter_v1"


def records():
    assets = pd.DataFrame(
        [
            dict(
                asset_key_candidate=str(i),
                area="北海道",
                hjks_business_name_unique_reference=n,
                hjks_business_name_reference_status=status,
            )
            for i, (n, status) in enumerate(
                [
                    ("北海道電力株式会社", "scope_checked_unique_reference"),
                    ("北海道パワーエンジニアリング株式会社", "scope_checked_unique_reference"),
                    ("不明な発電株式会社", "scope_checked_unique_reference"),
                    ("北海道電力株式会社", "ambiguous_unit_candidates"),
                ]
            )
        ]
    )
    registry = pd.read_csv(
        REPO / "metadata/research/hokkaido_business_reference_classification_20261016.csv",
        dtype=str,
        keep_default_na=False,
    )
    targets = pd.read_csv(REPO / "metadata/research/regulatory_targets.csv", keep_default_na=False)
    return assets, registry, targets


def test_company_identity_not_geography_or_group():
    assets, registry, targets = records()
    result = classify_assets(assets, registry, targets)
    assert result.reference_classification.tolist() == [
        "target",
        "non_target",
        "unknown",
        "unknown",
    ]
    assert not result.final_supplier_classification_assigned.any()
    pd.testing.assert_frame_equal(result[assets.columns], assets)


def test_unverified_identity_and_incomplete_period_stay_unknown():
    assets, registry, targets = records()
    registry.loc[
        registry.hjks_business_name_raw.eq("北海道パワーエンジニアリング株式会社"),
        "name_identity_verified",
    ] = "false"
    assert classify_assets(assets, registry, targets).reference_classification.iloc[1] == "unknown"
    targets.loc[targets.market_area.eq("北海道"), "valid_to"] = "2025-10-31"
    assert classify_assets(assets, registry, targets).reference_classification.iloc[0] == "unknown"


def test_duplicate_company_registry_is_not_silently_resolved():
    assets, registry, targets = records()
    with pytest.raises(ValueError, match="重複"):
        classify_assets(assets, pd.concat([registry, registry.iloc[:1]]), targets)


def unit_frame():
    return pd.DataFrame(
        [
            dict(
                asset_key_candidate="a",
                delivery_date=date(2026, 3, 31),
                period=p,
                generation_kwh_30min=g,
                capacity_reference_kw=D(100),
                capacity_gap_reference_kw=gap,
                research_use_status="use_diagnostic",
            )
            for p, g, gap in [(1, D(60), D(-20)), (48, D(0), D(100)), (2, None, None)]
        ]
    )


def test_conversion_negative_zero_and_missing_preserved():
    units = unit_frame()
    before = units.copy(deep=True)
    mapping = pd.DataFrame([dict(asset_key_candidate="a", reference_classification="non_target")])
    out, qa = add_unit_reference(units, mapping)
    assert out.non_target_reference_gap_mw.tolist() == [D("-.02"), D(".1"), None]
    assert qa["matched"] == 3 and qa["unmatched"] == 0
    pd.testing.assert_frame_equal(units, before)
    agg = aggregate_reference(out, expected_units=2).set_index("period")
    assert agg.loc[1, "R_ref_MW"] == D("-.02")
    assert agg.loc[1, "reference_sum_is_partial"]
    assert pd.isna(agg.loc[1, "R_ref_complete_MW"])
    assert pd.isna(agg.loc[2, "R_ref_MW"])
    assert agg.loc[2, "non_target_output_missing_count"] == 1
    assert agg.loc[2, "non_target_capacity_held_count"] == 0
    assert agg.loc[48, "non_target_zero_output_gap_mw"] == D(".1")
    assert nullable_sum([None, float("nan")]) is None


def test_reference_join_missing_and_row_growth_rejected():
    units = unit_frame()
    mapping = pd.DataFrame([dict(asset_key_candidate="other", reference_classification="unknown")])
    with pytest.raises(ValueError, match="未一致"):
        add_unit_reference(units, mapping)
    units = pd.concat([units, units.iloc[:1]])
    with pytest.raises(ValueError, match="重複"):
        add_unit_reference(units, mapping)


def test_forecast_quartile_ties_missing_and_empty_groups():
    demand = pd.Series([D(1)] * 6 + [D(2)] * 3 + [D(3)] + [None])
    labels, qa = tied_quartiles(demand)
    assert labels.iloc[:6].nunique() == 1
    assert pd.isna(labels.iloc[-1]) and qa["null_count"] == 1
    assert qa["groups"] < 4
    assert sum(r["count"] for r in qa["rows"]) == 10
    labels, qa = tied_quartiles(pd.Series([D(5)] * 48))
    assert labels.nunique() == 1 and qa["groups"] == 1


def network_frame():
    return pd.DataFrame(
        [
            dict(
                delivery_date=date(2026, 3, 31),
                period=48,
                sending_area="東北",
                receiving_area="北海道",
                direction_status="official_orientation_verified",
                source_variant="system_capacity_dayahead2_planned",
                link_name_raw="北海道・本州間電力連系設備",
            )
        ]
    )


def test_multiple_imports_and_wrong_direction_not_aggregated():
    n = network_frame()
    assert len(select_import(n)) == 1
    with pytest.raises(ValueError, match="重複"):
        select_import(pd.concat([n, n]))
    n.sending_area = "北海道"
    n.receiving_area = "東北"
    with pytest.raises(ValueError, match="方向なし"):
        select_import(n)


def test_unverified_import_not_used():
    n = network_frame()
    n.direction_status = "manual_review"
    with pytest.raises(ValueError, match="方向未確認"):
        select_import(n)


def test_real_hokkaido_source_formula_and_full_grid():
    path = OUTPUT / "hokkaido_scatter_panel_FY2025.parquet"
    if not path.exists():
        pytest.skip("ローカルの北海道記述版なし")
    panel = pq.read_table(path).to_pandas()
    units = pq.read_table(OUTPUT / "hokkaido_unit_reference_FY2025.parquet").to_pandas()
    assert len(panel) == 17520 and panel.delivery_date.nunique() == 365
    assert not panel.duplicated(KEY).any()
    assert panel.groupby("delivery_date").period.apply(lambda s: set(s) == set(range(1, 49))).all()
    assert set(panel.timezone) == {"Asia/Tokyo"}
    assert str(panel.timestamp_end.iloc[-1]) == "2026-04-01 00:00:00+09:00"
    selected = units[units.reference_classification.eq("non_target")]
    values = (selected.capacity_reference_kw - selected.generation_kwh_30min * 2) / 1000
    expected = selected.assign(independent_gap=values).groupby(KEY).independent_gap.sum()
    assert expected.tolist() == panel.R_ref_MW.tolist()
    assert selected.asset_key_candidate.nunique() == 3
    assert panel.non_target_calculated_unit_count.eq(3).all()
    assert panel.main_plot_eligible.all()
    assert panel.import__direction_raw.eq("逆方向").all()
    assert (
        panel.import_capacity_direction_display_mw.tolist()
        == (-panel.import__available_capacity_mw).tolist()
    )
    assert (
        panel.hokkaido_minus_tohoku_price.tolist()
        == (panel.area_price - panel.area_price_tohoku).tolist()
    )
    assert set(panel.price_difference_sign) == {"positive", "zero", "negative"}
    original = pq.read_table(
        ROOT / A_PATH / "generation_with_snapshot_public_records_FY2025.parquet",
        filters=[("area", "=", "北海道")],
    )
    reread = pq.read_table(
        OUTPUT / "hokkaido_unit_reference_FY2025.parquet", columns=original.column_names
    )
    assert original.equals(reread, check_metadata=False)


def test_connection_retains_missing_plot_values_and_rejects_time_mismatch():
    from src.hokkaido_scatter import connect_panel

    start = pd.Timestamp("2026-03-31 00:00", tz="Asia/Tokyo")
    market = pd.DataFrame(
        [
            dict(
                delivery_date=date(2026, 3, 31),
                period=p,
                timestamp_start=start,
                timestamp_end=start + pd.Timedelta(minutes=30),
                timezone="Asia/Tokyo",
                area_price=D("1"),
                area_price_tohoku=D("2"),
                reserve_nextday2__area_demand_forecast_mw=D("100"),
            )
            for p in [1, 2]
        ]
    )
    agg = market[KEY].copy()
    agg["R_ref_MW"] = [None, D(-1)]
    n = network_frame()
    n.period = 1
    n["timestamp_start"] = start
    n["timestamp_end"] = start + pd.Timedelta(minutes=30)
    n["timezone"] = "Asia/Tokyo"
    n["available_capacity_direction_display_mw"] = D("-5")
    panel, qa = connect_panel(market, agg, n)
    assert len(panel) == 2 and not panel.main_plot_eligible.any()
    assert panel.main_plot_unavailable_reasons.tolist() == [
        "R_ref_missing",
        "import_display_missing",
    ]
    assert qa["joins"][1]["unmatched"] == 1
    assert panel.import_capacity_direction_display_mw.iloc[0] == D(-5)
    assert panel.hokkaido_minus_tohoku_price.eq(-1).all()
    n.timestamp_start = start + pd.Timedelta(minutes=30)
    with pytest.raises(ValueError, match="対象時刻不一致"):
        connect_panel(market, agg, n)


def test_empty_source_list_parquet_roundtrip(tmp_path):
    from src.hokkaido_scatter import save_parquet

    frame = pd.DataFrame({"source_empty_list": [[], []], "R_ref_MW": [None, D("-0.25")]})
    path = tmp_path / "nested.parquet"
    saved = save_parquet(frame, path)
    restored = pq.read_table(path)
    assert saved["rows"] == 2
    assert restored["R_ref_MW"].to_pylist() == [None, D("-0.250000")]
    assert restored["source_empty_list"].to_pylist() == [[], []]
