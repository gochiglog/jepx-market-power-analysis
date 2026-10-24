"""公表記録の非加算・境界・未知・原観測保存の契約。"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.public_record_annotation import (
    BOUNDARY,
    FULL,
    HORIZON,
    INVALID,
    STATE_ID,
    UNKNOWN,
    VERSIONS,
    add_times,
    aggregate_profile,
    annotate_profile,
    classify_context,
    prepare_records,
    record_spans,
    sweep_asset,
)


def event(row=2, version=VERSIONS[0], **changes):
    raw = dict(
        エリア="東京",
        発電所コード="123",
        ユニット名="1号",
        発電所名="発電所",
        停止日時="2025/04/01 08:00",
        復旧予定日="2025/04/02",
        復旧見通し="あり",
        停止区分="計画停止",
        種別="停止・定期検査等",
        最終更新日時="2025/03/30 12:00",
        低下量="",
    )
    raw.update(changes)
    return dict(
        snapshot=version,
        source_row=row,
        source_sha256=version,
        source_file=version + ".csv",
        candidate_aids=[0],
        raw=raw,
    )


def test_exact_copies_remain_traceable_without_multiple_event_classification():
    contents, originals, versions = prepare_records([event(2), event(3), event(4, VERSIONS[1])])
    assert len(contents) == 1 and len(originals) == 3
    rows = versions[VERSIONS[0]]
    assert len(next(iter(rows.values()))["row_ids"]) == 2
    cid = next(iter(rows))
    assert classify_context({cid: FULL}, rows)[0] == "matched_planned_stop"
    assert len(versions[VERSIONS[1]]) == 1


def test_same_key_revisions_do_not_choose_latest_or_largest():
    _, _, v = prepare_records([event(2), event(3, 復旧予定日="2025/04/05", 低下量="10")])
    rows = v[VERSIONS[0]]
    assert all(r["update_candidate"] for r in rows.values())
    assert classify_context({c: FULL for c in rows}, rows)[0] == "held_update_candidates"


def test_distinct_overlapping_records_are_a_separate_primary_state():
    _, _, v = prepare_records([event(2), event(3, 停止日時="2025/04/01 09:00")])
    rows = v[VERSIONS[0]]
    assert classify_context({c: FULL for c in rows}, rows)[0] == "held_multiple_records"


def test_no_record_is_not_an_availability_assertion():
    assert classify_context({}, {})[:2] == ("no_applicable_record", 0)


def test_unknown_recovery_creates_hold_only_not_full_stop_interval():
    spans = record_spans(event(復旧予定日="", 復旧見通し="なし")["raw"], "early")
    assert spans == [(16, HORIZON, UNKNOWN)]
    _, _, v = prepare_records([event(復旧予定日="", 復旧見通し="なし")])
    cid = next(iter(v[VERSIONS[0]]))
    assert classify_context({cid: UNKNOWN}, v[VERSIONS[0]])[:2] == ("held_unknown_recovery", -1)


def test_same_day_endpoints_differ_but_recovery_day_remains_held():
    _, _, v = prepare_records([event(復旧予定日="2025/04/01")])
    rows = v[VERSIONS[0]]
    a = []
    b = []
    es, _, em = sweep_asset(rows, "early", a, VERSIONS[0], 0)
    ls, _, lm = sweep_asset(rows, "late", b, VERSIONS[0], 0)
    assert np.array_equal(es, ls)
    assert es[16] == STATE_ID["held_date_boundary"]
    assert int((em == 1).sum()) == 0 and int((lm == 1).sum()) == 32
    assert es[15] == STATE_ID["no_applicable_record"]


def test_partial_start_and_month_boundary_do_not_fill_a_half_hour():
    raw = event(停止日時="2025/04/30 23:40", 復旧予定日="2025/05/02")["raw"]
    spans = record_spans(raw, "early")
    assert (1439, 1440, BOUNDARY) in spans
    assert (1440, 1488, FULL) in spans


def test_invalid_dates_are_not_repaired_or_dropped():
    spans = record_spans(event(停止日時="bad")["raw"], "late")
    assert spans == [(0, HORIZON, INVALID)]
    assert all(
        f != FULL for _, _, f in record_spans(event(復旧予定日="2025/03/30")["raw"], "early")
    )


def test_unknown_and_revision_flags_remain_even_with_precedence():
    _, _, v = prepare_records([event(), event(3, 復旧予定日="", 復旧見通し="なし")])
    rows = v[VERSIONS[0]]
    members = {c: UNKNOWN for c in rows}
    state, _, flags = classify_context(members, rows)
    assert state == "held_unknown_recovery" and flags["has_update_candidates"]
    assert flags["has_multiple_contents"]


def test_reduction_parse_failures_remain_flagged_not_silent_nan():
    c, originals, _ = prepare_records([event(低下量="NaN")])
    r = next(iter(c.values()))
    assert r["published_mean_reduction_kw"] is None and r["qa_reduction_numeric_parse_failed"]
    assert originals[0]["低下量"] == "NaN"


def test_missing_and_capacity_hold_preserved_in_exclusive_partial_sums():
    df = pd.DataFrame(
        dict(
            aid=[0, 1, 2, 0, 1, 2],
            slot=[0, 0, 0, 1, 1, 1],
            area=["東京"] * 6,
            gen=[0.0, 20.0, np.nan, np.nan, np.nan, np.nan],
            cap=[100.0, np.nan, 50.0, 100.0, np.nan, 50.0],
            gap=[100.0, np.nan, np.nan, np.nan, np.nan, np.nan],
        )
    )
    state = np.array([7, 0, 11, 7, 0, 11])
    match = np.array([1, -1, 0, 1, -1, 0])
    d, g, totals = aggregate_profile(df, state, match)
    assert len(d) == 6 and totals.observed_unit_count.tolist() == [3, 3]
    assert totals.capacity_hold_count.tolist() == [1, 1]
    assert totals.missing_output_count.tolist() == [1, 3]
    assert totals.reference_gap_partial_sum_kw.iloc[0] == 100
    assert np.isnan(totals.reference_gap_partial_sum_kw.iloc[1])
    assert np.isnan(g[g.state_id == 0].reference_capacity_known_kw).all()


def test_asset_mapping_hold_and_original_missing_rows_are_not_dropped():
    mapping = [
        dict(research_use_status="held", research_use_reason="未対応"),
        dict(research_use_status="use_diagnostic", research_use_reason="使用可"),
    ]
    df = pd.DataFrame(
        dict(aid=[0, 1], slot=[0, 0], period_bad=[False, True], boundary=[False, False])
    )
    state, _, match = annotate_profile({}, mapping, df, "early", VERSIONS[0], [])
    assert state.tolist() == [0, STATE_ID["held_asset_period"]] and match.tolist() == [-1, -1]


def test_market_time_keys_keep_jst_24h_boundary():
    f = add_times(pd.DataFrame(dict(slot=[47, HORIZON - 1], area=["東京"] * 2)))
    assert str(f.timestamp_end.iloc[1]) == "2026-04-01 00:00:00+09:00"
    assert f.period.tolist() == [48, 48]
    assert str(f.delivery_date.iloc[1]) == "2026-03-31"


def test_annual_output_contract_when_available():
    repo = Path(__file__).resolve().parents[1]
    path = repo / "reports/public_record_annotation_qa_20261016.json"
    if not path.exists():
        pytest.skip("全対象実行前")
    q = json.loads(path.read_text())
    assert q["original_rows"] == 8468544 and q["annotation_rate"] == 1
    assert q["original_columns_all_cells_equal"] and q["dropped_rows"] == q["duplicate_key"] == 0
    assert q["original_record_rows"] == 66680 and q["market_join_rows"] == 4 * 10 * 365 * 48
    assert q["regional_partition_reconciled"] and not q["numerical_loss_deduction"]
    assert all(sum(p["state_counts"].values()) == 8468544 for p in q["profile_qa"])


def test_market_join_requires_profile_selection_and_one_row_per_key(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.public_record_annotation import read_market_profile

    path = tmp_path / "market.parquet"
    rows = [
        dict(snapshot=v, scenario=s, area="東京", delivery_date="2025-04-01", period=1)
        for v in VERSIONS
        for s in ("early", "late")
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)
    assert len(read_market_profile(path, snapshot=VERSIONS[0], scenario="early")) == 1
    with pytest.raises(ValueError):
        read_market_profile(path, snapshot=None, scenario="early")
    pq.write_table(pa.Table.from_pylist(rows + [rows[0]]), path)
    with pytest.raises(ValueError):
        read_market_profile(path, snapshot=VERSIONS[0], scenario="early")


def test_empty_state_counts_are_zero_but_quantities_are_null():
    from src.public_record_annotation import STATES, WIDE_METRICS, market_state_columns

    row = dict(area="東京", slot=0, primary_state="no_applicable_record")
    row.update({c: 1.0 for c in WIDE_METRICS})
    result = market_state_columns(pd.DataFrame([row]))
    assert len([c for c in result if "__" in c]) == len(STATES) * len(WIDE_METRICS)
    assert result["matched_planned_stop__observed_unit_count"].iloc[0] == 0
    assert np.isnan(result["matched_planned_stop__reference_gap_partial_sum_kw"].iloc[0])


def test_integration_frozen_annotation_states_and_original_values():
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from src.gap_interpretation import check_frozen
    from src.time_normalization import sha256

    repo = Path(__file__).resolve().parents[1]
    root = repo.parents[1] if repo.parent.name == ".worktrees" else repo
    p = repo / "metadata/research/public_record_annotation_frozen_20261016.json"
    if not p.exists() or not (root / "data/raw/occto/generation").exists():
        pytest.skip("Git管理外の研究データなし")
    manifest = json.loads(p.read_text())
    for r in manifest["outputs"]:
        assert sha256(root / r["path"]) == r["sha256"]
    assert len(check_frozen(root, repo)["files"]) == 8
    out = Path(
        json.loads((repo / "reports/public_record_annotation_qa_20261016.json").read_text())[
            "output"
        ]
    )
    original = (
        root
        / "data/interim/occto/reference_capacity_gap/20261016_diagnostic_v1"
        / "reference_capacity_gap_FY2025.parquet"
    )
    panel = pq.ParquetFile(out / "generation_with_snapshot_public_records_FY2025.parquet")
    source = pq.ParquetFile(original)
    columns = [
        "asset_key_candidate",
        "generation_kwh_30min",
        "capacity_gap_reference_kw",
        "timestamp_end",
    ]
    for a, b in zip(
        source.iter_batches(columns=columns), panel.iter_batches(columns=columns), strict=True
    ):
        assert pa.Table.from_batches([a]).equals(pa.Table.from_batches([b]), check_metadata=False)
    q = json.loads((repo / "reports/public_record_annotation_qa_20261016.json").read_text())
    for profile in q["profile_qa"]:
        name = (
            "pubrec_"
            + profile["snapshot"].rsplit("_", 1)[-1]
            + "_"
            + profile["scenario"]
            + "_state"
        )
        counts = pc.value_counts(panel.read(columns=[name])[name]).to_pylist()
        assert {r["values"]: r["counts"] for r in counts} == profile["state_counts"]
