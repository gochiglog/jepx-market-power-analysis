"""状態の境界・予定日の不確実性・原値の追跡を検証する。"""

import csv
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.gap_interpretation import check_frozen, consecutive_runs, time_label, zero_diagnosis
from src.generation_hjks import read_raw
from src.outage_case_studies import date_only_scenarios, event_identity_candidate

REPO = Path(__file__).resolve().parents[1]


def test_zero_runs_do_not_bridge_missing_or_absent_observations():
    assert consecutive_runs([0, 1, 2, 4, 5, 6], [True, True, False, True, True, True]) == [
        (0, 2, 2),
        (4, 7, 3),
    ]


@pytest.mark.parametrize("positions", [[1, 1], [2, 1]])
def test_invalid_time_order_rejected(positions):
    with pytest.raises(ValueError):
        consecutive_runs(positions, [True, True])


def test_zero_null_and_capacity_hold_stay_distinct(tmp_path):
    # 全空セル設備を全ゼロにしない。ゼロでも容量不明なら寄与に入れない。
    mapping = [
        dict(
            asset_key_candidate=str(i),
            area="関西",
            plant_name="p",
            unit_name=str(i),
            capacity_reference_raw_kw="100",
            research_use_status="held",
        )
        for i in range(3)
    ]
    df = pd.DataFrame(
        dict(
            aid=[0, 0, 1, 1, 2, 2],
            slot=[0, 1] * 3,
            gen=[0, 0, np.nan, np.nan, 0, 10],
            cap=[np.nan] * 4 + [100, 100],
            gap=[np.nan] * 4 + [100, 90],
            fuel=[0] * 6,
            area=["関西"] * 6,
        )
    )
    df["zero"] = df.gen.eq(0)
    df["positive"] = df.gen.gt(0)
    df["missing"] = df.gen.isna()
    df["calculable"] = df.gap.notna()
    df["zero_capacity_kw"] = np.where(df.zero & df.calculable, df.cap, 0)
    states, _, kansai, _ = zero_diagnosis(df, mapping, {0: ""}, tmp_path, tmp_path)
    assert [r["state"] for r in states] == [
        "全数値観測がゼロ",
        "数値観測なし",
        "ゼロと正の出力を観測",
    ]
    assert kansai[1]["both_raw_missing_cap_held"] == 2
    assert kansai[0]["raw_present_cap_held"] == 2
    groups = list(csv.DictReader((tmp_path / "zero_output_fuel_area_20261016.csv").open()))
    assert int(groups[0]["calculable_zero_cells"]) == 1
    assert float(groups[0]["zero_asset_distinct_reference_kw"]) == 100


def test_same_day_recovery_has_no_full_cells_without_endpoint_assumption():
    slots = np.arange(48)
    masks = date_only_scenarios("2025/04/01 08:00", "2025/04/01", slots)
    assert masks["full_cell_early"].sum() == 0
    assert masks["full_cell_late"].sum() == 32
    assert masks["recovery_date_uncertain"].sum() == 48
    assert masks["endpoints_are_assumptions"] is True


def test_partial_start_and_year_boundary_not_rounded_into_known_interval():
    start_slot = (date(2026, 1, 1) - date(2025, 4, 1)).days * 48 - 1
    masks = date_only_scenarios(
        "2025/12/31 23:40", "2026/01/02", np.arange(start_slot, start_slot + 98)
    )
    assert masks["partial_start_cell"][0]
    assert not masks["full_cell_early"][0]
    assert masks["full_cell_early"].sum() == 48
    assert time_label(start_slot + 1) == "2026-01-01T00:00:00+09:00"


def test_recovery_date_before_start_is_not_silently_repaired():
    with pytest.raises(ValueError, match="順序矛盾"):
        date_only_scenarios("2025/04/02 08:00", "2025/04/01", [0])


def test_candidate_identity_does_not_resolve_revised_rows():
    raw = dict(
        エリア="東京",
        発電所コード="123",
        ユニット名="1号",
        停止日時="2025/04/01 08:00",
        停止区分="計画停止",
        種別="停止・定期検査等",
        復旧予定日="2025/04/02",
    )
    revised = {**raw, "復旧予定日": "2025/05/01"}
    rows = [{"raw": raw}, {"raw": revised}]
    assert event_identity_candidate(rows[0]) == event_identity_candidate(rows[1])
    assert len(rows) == 2 and rows[0]["raw"]["復旧予定日"] != rows[1]["raw"]["復旧予定日"]


def test_real_fixed_qa_and_spike_original_totals():
    q = json.loads((REPO / "reports/reference_gap_interpretation_qa_20261016.json").read_text())
    assert q["cells"] == 8468544 and q["calculable_zero_cells"] == 4031821
    assert q["generation_missing_cells"] == 214299 and q["negative_gap_cells"] == 57860
    assert q["nuclear_negative_cells"] == 50302
    rows = list(csv.DictReader((REPO / "reports/top5_exceedance_raw_cases_20261016.csv").open()))
    assert len(rows) == 5 and all(r["daily_sum_matches"] == "True" for r in rows)
    assert all(r["raw_unit"] == "kWh/30分" for r in rows)


def test_case_no_drop_no_correction_and_mean_reduction_counterexample():
    q = json.loads((REPO / "reports/outage_case_studies_qa_20261016.json").read_text())
    assert q["case_count"] == 6 and q["original_event_rows"] == 14
    assert q["timeline_key_duplicates"] == q["dropped_rows"] == 0
    assert q["provenance_match_rate"] == 1
    assert not q["nationwide_correction_created"] and not q["actual_recovery_imputed"]
    rows = list(csv.DictReader((REPO / "reports/outage_case_comparison_20261016.csv").open()))
    sunagawa = next(r for r in rows if r["case_id"] == "reduction_sunagawa")
    assert int(sunagawa["early_above_capacity_minus_mean_cells"]) == 2433
    revision = [r for r in rows if r["case_id"] == "revision_okukiyotsu"]
    assert len(revision) == 4 and len({r["recovery_date_raw"] for r in revision}) == 2


def test_integration_fixed_source_and_exact_five_raw_cells():
    root = REPO.parents[1] if REPO.parent.name == ".worktrees" else REPO
    if not (root / "data/raw/occto/generation").exists():
        pytest.skip("研究rawはGit管理外")
    assert len(check_frozen(root, REPO)["files"]) == 8
    rows = list(csv.DictReader((REPO / "reports/top5_exceedance_raw_cases_20261016.csv").open()))
    cache = {}
    for r in rows:
        if r["raw_file"] not in cache:
            _, original, _ = read_raw(
                root, {"local_path": r["raw_file"], "sha256": r["raw_sha256"]}
            )
            cache[r["raw_file"]] = {n: row for n, row, _ in original}
        raw = cache[r["raw_file"]][int(r["source_row"])]
        assert raw[r["raw_column"]] == r["raw_kwh_30min"]
        assert raw["日量[kWh]"] == r["daily_raw_kwh"]
