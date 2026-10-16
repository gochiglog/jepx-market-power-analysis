"""少数の公表停止行と観測出力を照合。補正panelや実復旧時刻は作らない。"""

import json
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.asset_analysis_readiness import target_key
from src.generation_hjks import read_raw
from src.time_normalization import sha256
from src.unmatched_generation import write_csv

EPOCH = datetime(2025, 4, 1)


def load_event_candidates(root, repo, mapping):
    prior = json.loads((repo / "reports/generation_hjks_FY2025_qa.json").read_text())
    master_source = prior["snapshots"]["hjks_unit_677"]["source"]
    _, master_rows, _ = read_raw(root, master_source)
    master = {f"{master_source['sha256']}:{n}": r for n, r, _ in master_rows}
    target_to_aid = defaultdict(list)
    for aid, r in enumerate(mapping):
        if r["research_use_status"] == "use_diagnostic":
            target_to_aid[target_key(master[r["target_snapshot_row_id"]])].append(aid)
    events = []
    for label in ("hjks_outage_58852", "hjks_outage_7828"):
        source = prior["snapshots"][label]["source"]
        _, rows, _ = read_raw(root, source)
        for n, row, _ in rows:
            candidates = target_to_aid.get(target_key(row), [])
            events.append(
                dict(
                    snapshot=label,
                    source_file=source["local_path"],
                    source_sha256=source["sha256"],
                    source_row=n,
                    candidate_aids=candidates,
                    raw=row,
                )
            )
    return events


def date_only_scenarios(start_raw, recovery_raw, slots):
    """日付しかない復旧の早端/遅端を明示した仮定。観測時刻ではない。"""
    start = datetime.strptime(start_raw, "%Y/%m/%d %H:%M")
    end_early = datetime.strptime(recovery_raw, "%Y/%m/%d")
    end_late = end_early + timedelta(days=1)
    if start >= end_late:
        raise ValueError("停止開始と復旧予定日の順序矛盾")
    times = np.array(slots, dtype=float)
    start_slot = (start - EPOCH).total_seconds() / 1800
    early_slot = (end_early - EPOCH).total_seconds() / 1800
    late_slot = early_slot + 48
    return dict(
        # セル全区間が予定期間内にあるものだけ。開始の部分コマも別flag。
        full_cell_early=(times >= start_slot) & (times + 1 <= early_slot),
        full_cell_late=(times >= start_slot) & (times + 1 <= late_slot),
        recovery_date_uncertain=(times >= early_slot) & (times < late_slot),
        partial_start_cell=(times < start_slot) & (times + 1 > start_slot),
        start_datetime_raw=start_raw,
        recovery_date_raw=recovery_raw,
        endpoints_are_assumptions=True,
    )


def event_identity_candidate(event):
    """公式event IDではない。これを使って更新行を統合しない。"""
    r = event["raw"]
    return (*target_key(r), r["停止日時"], r["停止区分"], r["種別"])


# 選定過程は報告書に記録。論理CSV行番号とSHAを併記し別版の同じ行と混同しない。
CASE_SELECTION = [
    ("zero_ishikari", 2457, "長いゼロと停止予定の整合"),
    ("zero_futtsu", 2035, "長いゼロと停止予定の整合"),
    ("reduction_sunagawa", 157, "平均低下量と正の出力"),
    ("reduction_misumi", 229, "平均低下量と正の出力"),
    ("boundary_anegasaki", 342, "同日復旧予定の時刻不明"),
    ("revision_okukiyotsu", 628, "復旧予定が異なる重複更新候補"),
]


def make_cases(root, repo, mapping, df, output):
    events = load_event_candidates(root, repo, mapping)
    groups = defaultdict(list)
    for e in events:
        groups[event_identity_candidate(e)].append(e)
    raw_rows, summaries, timelines, daily = [], [], [], []
    selected_aids = set()
    for case, primary_row, purpose in CASE_SELECTION:
        primary = next(
            e
            for e in events
            if e["snapshot"] == "hjks_outage_7828" and e["source_row"] == primary_row
        )
        if len(primary["candidate_aids"]) != 1:
            raise ValueError("事例の設備対応が非一意")
        aid = primary["candidate_aids"][0]
        selected_aids.add(aid)
        a = mapping[aid]
        es = groups[event_identity_candidate(primary)]
        r = primary["raw"]
        start = datetime.strptime(r["停止日時"], "%Y/%m/%d %H:%M")
        recovery_dates = [datetime.strptime(e["raw"]["復旧予定日"], "%Y/%m/%d") for e in es]
        # 原行の両側1日を含め、FY2025で実際に観測したセルだけを見る。
        low = int(
            ((start.replace(hour=0, minute=0) - timedelta(days=1)) - EPOCH).total_seconds() / 1800
        )
        high = int(((max(recovery_dates) + timedelta(days=2)) - EPOCH).total_seconds() / 1800)
        g = df[(df.aid == aid) & (df.slot >= low) & (df.slot < high)].sort_values("slot")
        for e in es:
            er = e["raw"]
            masks = date_only_scenarios(er["停止日時"], er["復旧予定日"], g.slot)
            rid = f"{e['source_sha256']}:{e['source_row']}"
            raw_rows.append(
                dict(
                    case_id=case,
                    purpose=purpose,
                    asset_key_candidate=a["asset_key_candidate"],
                    snapshot=e["snapshot"],
                    source_file=e["source_file"],
                    source_sha256=e["source_sha256"],
                    source_row=e["source_row"],
                    row_id=rid,
                    event_identity_verified=False,
                    **er,
                )
            )
            early, late = masks["full_cell_early"], masks["full_cell_late"]
            loss = float(er["低下量"]) if er["低下量"] else None
            cap = float(a["capacity_reference_raw_kw"])
            effective = g[early]
            summaries.append(
                dict(
                    case_id=case,
                    purpose=purpose,
                    plant_name=a["plant_name"],
                    unit_name=a["unit_name"],
                    snapshot=e["snapshot"],
                    row_id=rid,
                    outage_type=er["停止区分"],
                    start_raw=er["停止日時"],
                    recovery_date_raw=er["復旧予定日"],
                    update_raw=er["最終更新日時"],
                    reference_capacity_kw=cap,
                    published_mean_reduction_kw=loss,
                    early_full_cells=int(early.sum()),
                    late_full_cells=int(late.sum()),
                    endpoint_assumption_difference_cells=int((late & ~early).sum()),
                    early_zero_cells=int(effective.zero.sum()),
                    early_positive_cells=int(effective.positive.sum()),
                    early_missing_cells=int(effective.missing.sum()),
                    late_zero_cells=int(g.loc[late, "zero"].sum()),
                    late_positive_cells=int((g.loc[late, "gen"] > 0).sum()),
                    late_missing_cells=int(g.loc[late, "missing"].sum()),
                    early_max_generation_avg_kw=float(effective.gen.max())
                    if effective.gen.notna().any()
                    else None,
                    early_above_capacity_minus_mean_cells=int((effective.gen > cap - loss).sum())
                    if loss is not None
                    else None,
                    candidate_identity_rows=len(es),
                    actual_recovery_verified=False,
                    initial_publication_verified=False,
                    correction_calculated=False,
                )
            )
            t = g[["aid", "slot", "gen", "cap", "gap", "missing", "zero"]].copy()
            t["case_id"], t["row_id"] = case, rid
            for key in (
                "full_cell_early",
                "full_cell_late",
                "recovery_date_uncertain",
                "partial_start_cell",
            ):
                t[key] = masks[key]
            timelines.append(t)
        # 日別表は元観測を1回だけ計数。イベント行の数を掛けて足さない。
        for day, d in g.groupby(g.slot // 48):
            daily.append(
                dict(
                    case_id=case,
                    asset_key_candidate=a["asset_key_candidate"],
                    delivery_date=str((EPOCH + timedelta(days=int(day))).date()),
                    observed_cells=len(d),
                    zero_cells=int(d.zero.sum()),
                    positive_cells=int(d.positive.sum()),
                    missing_cells=int(d.missing.sum()),
                    observed_generation_kwh=float(d.gen.sum(min_count=1) / 2)
                    if d.gen.notna().any()
                    else None,
                    numerical_event_correction_applied=False,
                )
            )
    timeline = pd.concat(timelines, ignore_index=True)
    if timeline.duplicated(["case_id", "row_id", "slot"]).any():
        raise ValueError("事例行×元コマ重複")
    source = (
        root
        / "data/interim/occto/reference_capacity_gap/20261016_diagnostic_v1"
        / "reference_capacity_gap_FY2025.parquet"
    )
    keys = [mapping[i]["asset_key_candidate"] for i in selected_aids]
    provenance = pq.read_table(
        source,
        filters=[("asset_key_candidate", "in", keys)],
        columns=[
            "asset_key_candidate",
            "delivery_date",
            "period",
            "source_file",
            "source_row",
            "generation_raw_value",
            "timestamp_start",
            "timestamp_end",
        ],
    ).to_pandas()
    id_index = {a["asset_key_candidate"]: i for i, a in enumerate(mapping)}
    provenance["aid"] = provenance.asset_key_candidate.map(id_index)
    provenance["slot"] = [
        (d - EPOCH.date()).days * 48 + int(p) - 1
        for d, p in zip(provenance.delivery_date, provenance.period)
    ]
    before = len(timeline)
    timeline = timeline.merge(
        provenance, on=["aid", "slot"], how="left", validate="many_to_one", indicator=True
    )
    if len(timeline) != before or not timeline._merge.eq("both").all():
        raise ValueError("事例の原発電行追跡失敗")
    timeline = timeline.drop(columns="_merge")
    pq.write_table(
        pa.Table.from_pandas(timeline, preserve_index=False),
        output / "outage_case_timeline.parquet",
        compression="zstd",
    )
    reports = repo / "reports"
    write_csv(reports / "outage_case_original_rows_20261016.csv", raw_rows)
    write_csv(reports / "outage_case_comparison_20261016.csv", summaries)
    write_csv(reports / "outage_case_daily_observations_20261016.csv", daily)
    qa = dict(
        case_count=len(CASE_SELECTION),
        original_event_rows=len(raw_rows),
        timeline_rows=len(timeline),
        timeline_key_duplicates=0,
        provenance_match_rate=1.0,
        dropped_rows=0,
        scenario_endpoints_are_assumptions=True,
        nationwide_correction_created=False,
        actual_recovery_imputed=False,
        timeline_sha256=sha256(output / "outage_case_timeline.parquet"),
        event_input_rows=len(events),
        event_candidate_unique_rows=sum(len(e["candidate_aids"]) == 1 for e in events),
        event_candidate_ambiguous_rows=sum(len(e["candidate_aids"]) > 1 for e in events),
        event_candidate_unmatched_rows=sum(not e["candidate_aids"] for e in events),
        event_candidate_row_match_rate=sum(len(e["candidate_aids"]) == 1 for e in events)
        / len(events),
    )
    (reports / "outage_case_studies_qa_20261016.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n"
    )
    return qa
