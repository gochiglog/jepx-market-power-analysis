"""比較用の頻度・符号・名称参照が、研究上の補完や帰属へ変わらないことを検証。"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src.comparison_hourly import PRICES, hourly_comparison, hourly_grid, price_pair
from src.comparison_interconnector import annotate_comparison
from src.operator_evidence import inventory_operators, lexical_name

D = Decimal
JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parents[1] if REPO.parent.name == ".worktrees" else REPO
OUTPUT = ROOT / "data/processed/comparison/20261016_decision_preparation_v2"


def small_hour(last_day=date(2026, 3, 31)):
    start = datetime.combine(last_day, datetime.min.time(), JST) + timedelta(hours=23)
    rows = []
    for i in range(2):
        rows.append(
            dict(
                area="東京",
                delivery_date=last_day,
                period=47 + i,
                timestamp_start=start + timedelta(minutes=30 * i),
                timestamp_end=start + timedelta(minutes=30 * (i + 1)),
                timezone="Asia/Tokyo",
                raw_price_text="10.00" if i == 0 else "13.01",
                **{c: D("10.00") if i == 0 else D("13.01") for c in PRICES},
            )
        )
    demand = pd.DataFrame(
        [
            dict(
                area="東京",
                delivery_date=last_day,
                hour_of_delivery=23,
                timestamp_start=start,
                timestamp_end=start + timedelta(hours=1),
                demand_hourly_csv_mwh=D("3.125"),
                source_row=25,
            )
        ]
    )
    return pd.DataFrame(rows), demand


def test_hourly_uses_both_halves_equal_duration_and_native_demand_fiscal_boundary():
    market, demand = small_hour()
    result, qa, unused = hourly_comparison(market, demand, areas=["東京"])
    row = result.iloc[-1]
    assert len(result) == 8760 and len(unused) == 0
    assert row.area_price__time_mean == D("11.505")
    assert row.area_price__minimum == D("10") and row.area_price__maximum == D("13.01")
    assert row.area_price__second_minus_first == D("3.01")
    assert row["demand_source__demand_hourly_csv_mwh"] == D("3.125")
    assert row["halfhour_1__raw_price_text"] == "10.00"
    assert row["halfhour_2__raw_price_text"] == "13.01"
    assert row.timestamp_end == datetime(2026, 4, 1, tzinfo=JST)
    assert row.delivery_date == date(2026, 3, 31)
    assert row.timestamp_end - row.timestamp_start == timedelta(hours=1)
    assert qa["joins"][-1]["matched"] == 1
    assert not result.iloc[0].two_halfhours_present
    assert result.iloc[0].area_price__time_mean is None


def test_missing_half_or_price_not_averaged_and_duplicate_not_dropped():
    market, demand = small_hour()
    result, _, _ = hourly_comparison(market.iloc[:1], demand, areas=["東京"])
    assert result.iloc[-1].area_price__time_mean is None
    assert result.iloc[-1].area_price__calculation_status == "missing_halfhour"
    market.loc[1, "area_price"] = None
    result, _, _ = hourly_comparison(market, demand, areas=["東京"])
    assert result.iloc[-1].area_price__calculation_status == "missing_price"
    with pytest.raises(ValueError, match="重複"):
        hourly_comparison(pd.concat([market, market]), demand, areas=["東京"])


def test_time_mismatch_out_of_scope_and_duplicate_demand_rejected():
    market, demand = small_hour()
    demand.loc[0, "timestamp_end"] += timedelta(minutes=30)
    with pytest.raises(ValueError, match="時間区間"):
        hourly_comparison(market, demand, areas=["東京"])
    market, demand = small_hour()
    with pytest.raises(ValueError, match="重複"):
        hourly_comparison(market, pd.concat([demand, demand]), areas=["東京"])
    fractional = market.copy()
    fractional["period"] = [47.5, 48]
    with pytest.raises(ValueError, match="不正"):
        hourly_comparison(fractional, demand, areas=["東京"])
    with pytest.raises(ValueError, match="年度・地域外"):
        hourly_comparison(market, demand, year=2024, areas=["東京"])


@pytest.mark.parametrize("year,days", [(2025, 365), (2023, 366)])
def test_hourly_grid_generalizes_leap_year_and_day_boundaries(year, days):
    grid = hourly_grid(year, ["東京"])
    assert len(grid) == days * 24
    assert grid.iloc[0].hour_of_delivery == 0 and grid.iloc[-1].hour_of_delivery == 23
    assert str(grid.timestamp_start.dt.tz) == "Asia/Tokyo"
    assert grid.groupby("delivery_date").size().eq(24).all()


def test_negative_price_pair_preserved_and_non_numeric_rejected():
    assert price_pair(D("-1"), D("-3")) == (D("-2"), D("-3"), D("-1"), D("-2"), D("2"))
    with pytest.raises(ValueError):
        price_pair("bad", D("1"))
    with pytest.raises(ValueError, match="非有限"):
        price_pair(D("NaN"), D("1"))


def network_fixture():
    return pd.DataFrame(
        [
            dict(
                source_record_id="r1",
                delivery_date=date(2025, 4, 1),
                period=1,
                available_capacity_mw=D("-4"),
                direction_raw="逆方向",
                sending_area="中部",
                receiving_area="東京",
                direction_status="official_orientation_verified",
            )
        ]
    )


def price_fixture():
    return pd.DataFrame(
        [
            dict(area=a, delivery_date=date(2025, 4, 1), period=1, area_price=D(v))
            for a, v in [("東京", "8.01"), ("中部", "6")]
        ]
    )


def test_system_only_sign_rule_and_receiving_minus_sending():
    source = network_fixture()
    result, qa = annotate_comparison(source, price_fixture(), system=True)
    assert source.available_capacity_mw.iloc[0] == D("-4")
    assert result.available_capacity_direction_display_mw.iloc[0] == D("4")
    assert result.receiving_minus_sending_price.iloc[0] == D("2.01")
    assert result.capacity_display_formula.iloc[0] == "available_capacity_mw * -1"
    assert qa["directions"]["source_row_direction"]["price_difference_available"] == 1
    source.loc[0, "available_capacity_mw"] = D("4")
    result, _ = annotate_comparison(source, price_fixture(), system=True)
    assert result.available_capacity_direction_display_mw.iloc[0] == D("-4")


def test_multiregion_or_unknown_sign_preserved_not_forced_into_single_area():
    source = network_fixture()
    source.loc[0, "sending_area"] = None
    result, qa = annotate_comparison(source, price_fixture(), system=True)
    assert result.receiving_minus_sending_price.iloc[0] is None
    assert qa["directions"]["source_row_direction"]["price_difference_available"] == 0
    source.loc[0, "direction_raw"] = "未確認"
    result, _ = annotate_comparison(source, price_fixture(), system=True)
    assert result.available_capacity_direction_display_mw.iloc[0] is None
    source.loc[0, "direction_raw"] = "逆方向"
    source.loc[0, "direction_status"] = "held_unverified"
    result, _ = annotate_comparison(source, price_fixture(), system=True)
    assert result.available_capacity_direction_display_mw.iloc[0] is None


def test_reserve_separate_direction_columns_never_absolute_valued():
    base = network_fixture().rename(
        columns={
            "sending_area": "forward__sending_area",
            "receiving_area": "forward__receiving_area",
            "direction_status": "forward__direction_status",
        }
    )
    base["reverse__sending_area"] = "東京"
    base["reverse__receiving_area"] = "中部"
    base["reverse__direction_status"] = "official_orientation_verified"
    base["forward_available_capacity_mw"] = D("-5")
    base["reverse_available_capacity_mw"] = D("-7")
    result, _ = annotate_comparison(base, price_fixture(), system=False)
    assert result.forward_available_capacity_comparison_mw.iloc[0] == D("-5")
    assert result.reverse_available_capacity_comparison_mw.iloc[0] == D("-7")
    assert result["reverse__receiving_minus_sending_price"].iloc[0] == D("-2.01")
    with pytest.raises(ValueError, match="重複"):
        annotate_comparison(base, pd.concat([price_fixture(), price_fixture()]), system=False)


def test_business_inventory_counts_each_asset_once_but_candidate_names_can_overlap():
    snapshot = dict(sha256="hash", local_path="reference.csv")
    raw = [
        (i, {"発電事業者": name, "発電所名": "試験", "ユニット名": str(i)}, [])
        for i, name in [(2, "株式会社JERA"), (3, "東京電力ホールディングス株式会社")]
    ]
    assets = pd.DataFrame(
        [
            dict(
                asset_key_candidate="a",
                area="東京",
                generation_sum="10",
                candidate_snapshot_rows="hash:2",
                plant_candidate_snapshot_rows="hash:2|hash:3",
            ),
            dict(
                asset_key_candidate="b",
                area="東京",
                generation_sum="20",
                candidate_snapshot_rows="",
                plant_candidate_snapshot_rows="hash:2|hash:3",
            ),
        ]
    )
    mapping = pd.DataFrame(
        [
            dict(
                asset_key_candidate="a",
                target_snapshot_row_id="hash:2",
                research_use_status="use_diagnostic",
            ),
            dict(asset_key_candidate="b", target_snapshot_row_id="", research_use_status="held"),
        ]
    )
    targets = pd.DataFrame(
        [
            dict(
                regulatory_entity="東京電力エナジーパートナー株式会社",
                market_area="東京",
                valid_from="2024-11-01",
                valid_to="2026-03-31",
                source_document="根拠",
                source_url="https://example.test",
                verification_level="official_search_verified",
            )
        ]
    )
    ledger, edges, exclusive, candidates, names, qa = inventory_operators(
        assets, mapping, raw, snapshot, targets
    )
    assert len(ledger) == 2 and sum(exclusive.observed_generation_kwh) == D("30")
    assert sum(candidates.observed_generation_kwh_candidate_range) > D("30")
    assert not ledger.fy2025_asset_business_relationship_verified.any()
    assert not names.asset_attribution_established.any()
    assert set(names.name_comparison) == {"different"}
    assert pd.isna(ledger.loc[1, "hjks_business_name_unique_reference"])
    assert qa["total_observed_generation_kwh"] == "30"
    assert len(edges) == 3
    assert lexical_name("ＪＥＲＡ ") == "JERA"
    assert lexical_name("中部電力(株)") != lexical_name("中部電力株式会社")


@pytest.mark.skipif(not OUTPUT.exists(), reason="ローカルの比較用派生データが必要")
def test_real_fy2025_comparison_outputs_keys_values_and_record_preservation():
    frame = pq.read_table(OUTPUT / "market_hourly_price_demand_FY2025.parquet").to_pandas()
    assert len(frame) == 78840
    assert not frame.duplicated(["area", "delivery_date", "hour_of_delivery"]).any()
    assert frame.delivery_date.nunique() == 365
    assert frame.groupby(["area", "delivery_date"]).size().eq(24).all()
    assert frame.two_halfhours_present.all() and frame.demand_source_key_matched.all()
    for name in PRICES:
        expected = (frame[f"halfhour_1__{name}"] + frame[f"halfhour_2__{name}"]) / 2
        assert frame[f"{name}__time_mean"].equals(expected)
    assert (
        int(
            (~frame.halfhour_1__baseline_price_match_flag).sum()
            + (~frame.halfhour_2__baseline_price_match_flag).sum()
        )
        == 2271 * 9
    )
    ledger = pq.read_table(OUTPUT / "observed_unit_business_evidence_FY2025.parquet").to_pandas()
    assert len(ledger) == 491 and not ledger.asset_key_candidate.duplicated().any()
    assert sum(ledger.generation_sum_kwh) == D("646505684424")
    assert not ledger.final_asset_attribution_assigned.any()
    qa = json.loads((REPO / "reports/11_decision_preparation/qa/qa.json").read_text())
    assert qa["immutability"]["all_passed"]
