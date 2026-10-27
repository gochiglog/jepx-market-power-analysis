from pathlib import Path

import pandas as pd
import pytest

from src.fixed_output_feasibility import (
    decode_csv,
    inspect_half_hours,
    interpret_subset_result,
    limited_supply_register,
)


def test_half_hour_original_cells_and_year_boundary():
    frame = pd.DataFrame({"value": ["0", "", "-2", "***"]})
    time = ["2025-12-31 23:00", "2025-12-31 23:30", "2026-01-01 00:00", "2026-01-01 00:30"]
    result, qa = inspect_half_hours(frame, time, ["value"])
    assert result["value"].tolist() == ["0", "", "-2", "***"]
    assert result["sample_period"].tolist() == [47, 48, 1, 2]
    assert str(result["sample_timestamp_end"].iloc[1]) == "2026-01-01 00:00:00+09:00"
    assert qa["missing_numeric_cells"] == {"value": 1}
    assert len(qa["non_numeric_cells"]) == 1
    assert qa["numeric_summary"]["value"]["minimum"] == "-2"
    assert len(qa["incomplete_days"]) == 2


def test_complete_day_and_duplicate_not_silent_drop():
    times = pd.date_range("2026-03-31", periods=48, freq="30min")
    frame = pd.DataFrame({"x": ["1"] * 48})
    _, qa = inspect_half_hours(frame, times, ["x"])
    assert qa["incomplete_days"] == []
    frame.loc[48] = ["1"]
    result, qa = inspect_half_hours(frame, list(times) + [times[0]], ["x"])
    assert len(result) == 49
    assert qa["duplicate_keys"] == 1
    assert qa["incomplete_days"] == ["2026-03-31"]


@pytest.mark.parametrize("time", ["2025-04-01 00:15", "2025-04-01 00:00:01"])
def test_non_boundary_rejected(time):
    with pytest.raises(ValueError):
        inspect_half_hours(pd.DataFrame({"x": ["1"]}), [time], ["x"])


def test_unknown_fuel_and_capacity_are_retained():
    assets = pd.DataFrame(
        {
            "asset_key_candidate": ["known", "other", "missing"],
            "発電方式・燃種": ["火力（ガス）", "その他", "原子力"],
            "reference_capacity_positive_unique": [True, True, False],
        }
    )
    result = limited_supply_register(assets)
    assert len(result) == 3
    assert result["limited_scope_status"].tolist() == [
        "candidate_known_reference_subset",
        "held_fuel_scope_unresolved",
        "held_capacity_or_equipment",
    ]
    assert not result["unknown_capacity_assumed_zero"].any()
    assert not result["actual_availability_verified"].any()
    with pytest.raises(ValueError):
        limited_supply_register(pd.concat([assets, assets]))


def test_subset_failure_is_not_regionwide_shortage():
    assert "no_regionwide_shortage_conclusion" in interpret_subset_result(False)
    assert "assumptions" in interpret_subset_result(True)


def test_real_hokuriku_sample_91_days_48_periods():
    path = Path(
        "data/interim/research_reference/20261026_R_feasibility_v1/hokuriku_fit_202504_06.csv"
    )
    if not path.exists():
        pytest.skip("ローカル参照サンプルなし")
    rows, _ = decode_csv(path.read_bytes())
    frame = pd.DataFrame(rows[1:], columns=rows[0])
    result, qa = inspect_half_hours(frame, frame["DATE_TIME"], rows[0][1:])
    assert qa["rows"] == 91 * 48
    assert qa["days"] == 91
    assert qa["duplicate_keys"] == 0
    assert qa["incomplete_days"] == []
    assert qa["non_numeric_cells"] == []
    assert list(result.columns[:5]) == rows[0]
    assert "作成" not in "".join(rows[0])  # 時刻欄のないファイルに予測断面を付与しない。


def test_mixed_date_storage_join_checks_all_pairs():
    from datetime import date

    from src.fixed_output_feasibility import select_candidate_period

    left = pd.DataFrame(
        {
            "area": ["北海道", "東北", "北海道", "東北"],
            "supply_scenario": ["base"] * 4,
            "delivery_date": [
                date(2025, 4, 1),
                date(2025, 4, 1),
                date(2026, 3, 31),
                date(2026, 3, 31),
            ],
            "period": [1] * 4,
        }
    )
    right = pd.DataFrame({"delivery_date": ["2025-04-01"], "period": [1]})
    selected, qa = select_candidate_period(left, right)
    assert len(selected) == 2
    assert qa["both"] == 2 and qa["left_only"] == 2
    # 条件の一部だけ欠けても成功扱いにしない。
    with pytest.raises(ValueError):
        select_candidate_period(left.iloc[1:], right)
