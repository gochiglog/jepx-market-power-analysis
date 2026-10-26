from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from src.actual_net_demand import (
    candidate_roles,
    clock_keys,
    net_candidate,
    numeric_raw,
    removal_balance_rhs,
)


def test_start_and_end_clock_share_delivery_day():
    a = clock_keys(["2025/4/1", "2025/4/1"], ["0:00", "23:30"], role="start")
    b = clock_keys(["20250401", "20250401"], ["0:30", "24:00:00"], role="end")
    pd.testing.assert_frame_equal(a, b)
    assert b.period.tolist() == [1, 48]
    assert str(b.timestamp_end.iloc[-1]) == "2025-04-02 00:00:00+09:00"
    assert str(b.delivery_date.iloc[-1]) == "2025-04-01"


@pytest.mark.parametrize("clock", ["24:30", "25:00", "0:15", "1:00:30"])
def test_invalid_clock_rejected(clock):
    with pytest.raises(ValueError):
        clock_keys(["2025/4/1"], [clock], role="end")


def test_missing_numeric_and_nonpositive_net_not_clipped():
    assert numeric_raw("") is None
    with pytest.raises(Exception):
        numeric_raw("***")
    assert net_candidate(Decimal("80"), Decimal("70"), Decimal("20")) == Decimal("-10")
    assert net_candidate(80, 70, None) is None
    assert net_candidate(80, 70, float("nan")) is None
    assert numeric_raw("1") / Decimal("500") == Decimal("0.002")  # kWh/30分 -> MW


def test_nuclear_leaves_adjustable_set_without_changing_417_register():
    frame = pd.DataFrame(
        {
            "asset_key_candidate": ["nuclear", "gas", "unknown"],
            "発電方式・燃種": ["原子力", "火力（ガス）", "その他"],
            "limited_scope_status": ["candidate_known_reference_subset"] * 2
            + ["held_fuel_scope_unresolved"],
        }
    )
    result = candidate_roles(frame)
    assert result.candidate_analysis_role.tolist() == [
        "fixed_observed_nuclear_candidate",
        "adjustable_reference_candidate",
        "held",
    ]
    pd.testing.assert_frame_equal(result[frame.columns], frame)
    assert not result.final_role_adopted.any()
    assert not result.technical_inflexibility_verified.any()


def test_firm_fixed_supply_removal_balance_at_lambda_one():
    # N0=80、固定供給の当該社分20。全供給除去なら必要調整量100。
    assert removal_balance_rhs(80, 20, removal_scope="adjustable_only") == 80
    assert removal_balance_rhs(80, 20, removal_scope="all_supply") == 100
    assert removal_balance_rhs(80, None, removal_scope="all_supply") is None
    assert removal_balance_rhs(80, None, removal_scope="adjustable_only") == 80
    assert removal_balance_rhs(-5, 2, removal_scope="all_supply") == -3


def test_actual_sample_integration_and_null_scope():
    path = Path(
        "data/interim/occto/actual_net_demand/20261026_preparation_v7/"
        "actual_sample_and_plan_candidates.parquet"
    )
    if not path.exists():
        pytest.skip("ローカル点検データなし")
    frame = pd.read_parquet(path)
    assert len(frame) == 9 * 30 * 48
    assert not frame.duplicated(["area", "delivery_date", "period"]).any()
    assert frame.plan_join.eq("both").all()
    chubu = frame[frame.area.eq("中部")]
    assert chubu.nuclear_regional_sample_mw.isna().all()
    assert chubu.candidate_net_B_regional_mw.isna().all()
    assert frame.groupby(["area", "delivery_date"]).size().eq(48).all()
    assert not frame.research_scope_adopted.any()
    # 制御量をRへ足していない。
    for area, group in frame.groupby("area"):
        if area != "中部":
            assert group.renewable_actual_sample_mw.eq(
                group.pv_actual_sample_mw + group.wind_actual_sample_mw
            ).all()


def test_rei_area_selected_from_header_not_filename():
    from src.actual_net_demand import regional_column_indices

    regions = ["", "", "北海道", "", "東京", ""]
    variables = ["日付", "時刻", "需要", "原子力", "需要", "原子力"]
    assert regional_column_indices(regions, variables, "東京", ["需要"]) == {"需要": 4}
    with pytest.raises(ValueError):
        regional_column_indices(regions, variables, "東北", ["需要"])


def test_hourly_energy_keeps_missing_half_hour_and_constraints():
    from src.actual_net_demand import hourly_energy_comparison

    frame = pd.DataFrame(
        {
            "area": ["東京"] * 3,
            "delivery_date": ["2025-12-31"] * 3,
            "period": [1, 2, 3],
            "demand_actual_sample_mw": [Decimal("100"), Decimal("200"), Decimal("300")],
            "renewable_actual_sample_mw": [Decimal("0"), Decimal("20"), Decimal("30")],
            "nuclear_regional_sample_mw": [None, None, None],
        }
    )
    result = hourly_energy_comparison(frame)
    assert len(result) == 2
    assert result.demand_actual_sample_mw_hour_mwh.iloc[0] == Decimal("150")
    assert result.demand_actual_sample_mw_hour_mwh.iloc[1] is None
    assert result.nuclear_regional_sample_mw_hour_mwh.isna().all()
    assert not result.network_constraints_averaged.any()
    assert not result.hourly_lp_ready.any()
