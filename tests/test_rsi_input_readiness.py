"""RSI設計の入力診断が制度対象・参照名・利用可能供給力を混同しないことを検査。"""

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.inspect_rsi_inputs import inspect

ROOT = Path(__file__).resolve().parents[1]


def test_rsi_input_readiness_preserves_unknown_control_and_one_firm_scope():
    if not (ROOT / "data/processed/comparison/20261016_decision_preparation_v2").exists():
        pytest.skip("研究データはGit管理外")
    pairs, qa = inspect(ROOT)
    assert len(pairs) == 11
    assert pairs.regulatory_entity.nunique() == 10
    assert pairs.controlled_available_supply_mw.isna().all()
    assert not pairs.asset_attribution_assigned.any()
    assert not pairs.rsi_computed.any()
    missing = pairs[
        pairs.regulatory_entity.isin(
            ["東京電力エナジーパートナー株式会社", "中部電力ミライズ株式会社"]
        )
    ]
    assert len(missing) == 2
    assert missing.scope_checked_name_reference_units_in_area.eq(0).all()
    assert missing.observed_generation_kwh_name_reference_in_area.isna().all()
    assert missing.name_reference_status.str.contains("供給ゼロではない").all()
    jera = pairs[pairs.regulatory_entity.eq("株式会社JERA")]
    assert len(jera) == 2
    assert jera.scope_checked_name_reference_units_all_nine_areas.eq(83).all()
    assert jera.scope_checked_name_reference_units_in_area.tolist() == [48, 35]
    assert qa["plan_demand_nine_area_rows"] == 9 * 365 * 48
    assert qa["plan_demand_nulls"] == qa["plan_demand_duplicate_keys"] == 0
    assert qa["hk_reference_non_target_units"] == 3
    assert qa["hk_reference_non_target_capacity_kw"] == 462000
    assert qa["planned_target_time_cells"] == 11 * 17520
    assert not qa["rsi_computed"]
    saved = json.loads((ROOT / "reports/15_rsi_design/qa/input_readiness.json").read_text())
    assert qa == saved
    table = pd.read_csv(
        ROOT / "reports/15_rsi_design/qa/target_pair_readiness.csv",
        dtype={"observed_generation_kwh_name_reference_in_area": str},
    )
    assert table.controlled_available_supply_mw.isna().all()
    pd.testing.assert_frame_equal(
        table.astype(object).where(table.notna(), None),
        pairs.astype(object).where(pairs.notna(), None),
        check_dtype=False,
    )
