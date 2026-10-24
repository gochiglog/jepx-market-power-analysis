"""候補確認状態と分析利用、分母、不足情報を混同しないことを検査する。"""

import json
from decimal import Decimal
from pathlib import Path
from shutil import copyfile

import pandas as pd
import pytest

from src.asset_analysis_readiness import (
    CONDITIONAL,
    HOLD,
    NO_CONFLICT,
    build_scenario,
    compare_coverage,
    diagnose_candidates,
    gap_diagnosis,
    left_join_diagnostics,
    run,
)


def asset(aid="a", strict=False):
    return dict(
        asset_key_candidate=aid,
        area="東京",
        plant_code=aid,
        plant_name_normalized=aid,
        plant_match=str(strict),
        unit_match=str(strict),
        candidate_snapshot_rows="r1" if strict else "",
        plant_candidate_snapshot_rows="r1",
    )


def evidence(aid="a", target="r1", state=NO_CONFLICT, code="GB553"):
    return dict(
        asset_key_candidate=aid,
        hjks_snapshot_row_id=target,
        analysis_use_diagnosis=state,
        generation_plant_code=code,
        verification_status="strongly_supported",
    )


def test_official_verification_state_is_not_promoted_by_analysis_diagnosis():
    e = evidence()
    m = {"r1": {"稼働開始日": "1900/01/01", "稼働終了日": "9999/12/31"}}
    result = diagnose_candidates([asset()], [e], m)
    assert result[0]["analysis_use_diagnosis"] == NO_CONFLICT
    assert result[0]["verification_status"] == "strongly_supported"
    assert not result[0]["fy_capacity_validity_verified"]
    assert result[0]["hjks_operation_start_raw"] == "1900/01/01"
    assert e["analysis_use_diagnosis"] == NO_CONFLICT


def test_reused_target_or_multiple_source_candidates_are_held():
    m = {r: {"稼働開始日": "1900/01/01", "稼働終了日": "9999/12/31"} for r in ["r1", "r2"]}
    conflict = diagnose_candidates([asset(), asset("b")], [evidence(), evidence("b")], m)
    assert all(r["analysis_use_diagnosis"] == HOLD for r in conflict)
    multiple = diagnose_candidates([asset()], [evidence(), evidence(target="r2")], m)
    assert all(r["analysis_use_diagnosis"] == HOLD for r in multiple)
    scenario = build_scenario([asset()], [evidence(state=HOLD)], True)
    assert len(scenario) == 1 and scenario[0]["held_candidate"]
    assert not scenario[0]["unit_matched"]
    assert json.loads(scenario[0]["proposed_candidate_ids_json"]) == ["r1"]


def test_conditional_candidate_keeps_condition_and_unmatched_source():
    scenario = build_scenario([asset(), asset("b")], [evidence(state=CONDITIONAL)], True)
    assert scenario[0]["analysis_use_diagnosis"] == CONDITIONAL
    assert scenario[0]["unit_matched"] and not scenario[0]["baseline_adopted"]
    assert not scenario[1]["unit_matched"]
    assert len(scenario) == 2


def day(aid="a", key="a:2025-04-01", value=Decimal(10), nulls=0):
    return dict(
        asset_key_candidate=aid,
        source_day_key=key,
        observed_kwh=value,
        area="東京",
        month="2025-04",
        null_cells=nulls,
    )


def test_left_join_rejects_multiplication_and_drop_and_preserves_null():
    scenario = build_scenario([asset()], [], False)
    joined = left_join_diagnostics([day(value=None, nulls=48)], scenario)
    assert joined[0]["observed_kwh"] is None and joined[0]["null_cells"] == 48
    with pytest.raises(pd.errors.MergeError):
        left_join_diagnostics([day()], scenario + scenario)
    with pytest.raises(ValueError, match="脱落"):
        left_join_diagnostics([day("missing")], scenario)
    with pytest.raises(ValueError, match="重複"):
        left_join_diagnostics([day(), day()], scenario)


def test_same_denominator_in_both_scenarios_and_missing_not_zero_observation():
    days = [day(), day("b", "b:2025-04-01", None, 48)]
    assets = [asset(), asset("b")]
    joined = []
    for include in (False, True):
        joined += left_join_diagnostics(days, build_scenario(assets, [evidence()], include))
    rows = [r for r in compare_coverage(joined) if r["dimension"] == "全国"]
    assert {r["total_observed_kwh"] for r in rows} == {"10"}
    assert all(r["null_cell_count"] == 48 and r["source_unit_days"] == 2 for r in rows)


def test_plant_code_match_is_not_a_unit_scope_match():
    source = dict(unit_name="広野６号", plant_name="広野火力発電所６号機", unit_candidate_count="0")
    pool = [{"ユニット名": "広野１号", "発電所名": "広野火力発電所１号機"}]
    assert gap_diagnosis(source, pool)[0] == "対応情報の不足"
    duplicate = dict(source, unit_candidate_count="2")
    assert gap_diagnosis(duplicate, pool)[0] == "対応情報の不足"
    component = [{"ユニット名": "７－１GTA", "発電所名": "西名古屋火力発電所"}]
    assert (
        gap_diagnosis(dict(source, unit_name="７-１号機"), component)[0]
        == "設備階層や集約範囲の違い"
    )


def test_real_scenarios_reproduce_coverage_and_preserve_all_observations(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    root = repo.parents[1] if repo.parent.name == ".worktrees" else repo
    if not (root / "data/interim/occto/generation/generation_long_FY2025.parquet").exists():
        pytest.skip("研究raw/longを持つ環境のみ")
    (tmp_path / "reports").mkdir()
    names = [
        "generation_hjks_677_mapping_candidates.csv",
        "generation_hjks_FY2025_qa.json",
        "top10_official_mapping_evidence_20261016.csv",
        "top10_official_review_qa_20261016.json",
    ]
    for name in names:
        copyfile(repo / "reports" / name, tmp_path / "reports" / name)
    q = run(root, tmp_path, tmp_path / "interim")
    assert q["analysis_use_counts"] == {NO_CONFLICT: 48, CONDITIONAL: 1, HOLD: 0}
    assert q["verification_status_counts"] == {"strongly_supported": 49}
    assert sum(Decimal(v["observed_kwh"]) for v in q["gap"].values()) == 120600148106
    assert sum(v["unit_count"] for v in q["gap"].values()) == 67
    for case in q["joins"].values():
        assert case["input_unit_days"] == case["output_unit_days"] == 176428
        assert case["represented_30min_rows"] == 8468544
        assert case["silent_row_drop"] == case["unintended_row_growth"] == 0
        assert case["parquet_roundtrip_equal"]
    lookup = {(r["scenario"], r["level"]): r for r in q["coverage"]}
    assert Decimal(lookup["strict_existing", "plant"]["matched_observed_kwh"]) == 518800283424
    assert (
        Decimal(lookup["candidate49_diagnostic", "plant"]["matched_observed_kwh"]) == 630814437424
    )
    for scenario in ("strict_existing", "candidate49_diagnostic"):
        assert (
            Decimal(lookup[scenario, "plant"]["matched_observed_kwh"])
            - Decimal(lookup[scenario, "unit"]["matched_observed_kwh"])
            == 120600148106
        )
    assert all(r["physical_capacity_coverage"] is None for r in q["capacity"])
    assert all(not r["as_of_complete_history_reconstructible"] for r in q["outage"])
    assert not q["baseline_selected"] and not q["headroom_calculated"]
