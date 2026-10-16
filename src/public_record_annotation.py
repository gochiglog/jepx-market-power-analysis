"""snapshot収録公表記録の案A付与。実停止・損失控除・市場時点を推定しない。"""

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.asset_analysis_readiness import load_csv
from src.gap_interpretation import check_frozen, time_label
from src.generation_hjks import read_raw
from src.outage_case_studies import EPOCH, event_identity_candidate, load_event_candidates
from src.time_normalization import sha256
from src.unmatched_generation import write_csv

HORIZON = 365 * 48
VERSIONS = ("hjks_outage_58852", "hjks_outage_7828")
STATES = [
    "mapping_unavailable",
    "held_asset_period",
    "held_invalid_dates",
    "held_unknown_recovery",
    "held_update_candidates",
    "held_multiple_records",
    "held_date_boundary",
    "matched_planned_stop",
    "matched_unplanned_stop",
    "matched_reduction",
    "matched_other",
    "no_applicable_record",
]
STATE_LABELS = [
    "設備対応不能/範囲未確認のため照合不可",
    "設備同一性の期間矛盾/境界により保留",
    "日時不正/前後矛盾により保留",
    "復旧予定不明/見通し矛盾により保留",
    "同一イベントか不明な更新候補により保留",
    "複数の異なる公表記録により保留",
    "開始部分コマ/復旧日の日内境界不明により保留",
    "snapshotの計画停止記録が予定シナリオに該当",
    "snapshotの計画外停止記録が予定シナリオに該当",
    "snapshotの出力低下記録が予定シナリオに該当",
    "その他の公表記録が予定シナリオに該当",
    "snapshot内に該当する記録が見つからない（停止なしではない）",
]
STATE_ID = {s: i for i, s in enumerate(STATES)}
TYPE_STATE = {"計画停止": 7, "計画外停止": 8, "出力低下": 9}
# 公表記録の関連理由。イベントの実在や稼働状態の真偽ではない。
FULL, BOUNDARY, UNKNOWN, INVALID = 1, 2, 4, 8
METRICS = [
    "observed_unit_count",
    "calculable_unit_count",
    "known_capacity_unit_count",
    "missing_output_count",
    "capacity_hold_count",
    "negative_gap_count",
    "zero_output_count",
    "calculable_zero_count",
    "full_scenario_record_unit_count",
    "scenario_match_unknown_unit_count",
    "reference_capacity_known_kw",
    "computed_reference_capacity_kw",
    "reference_gap_partial_sum_kw",
    "zero_reference_capacity_kw",
    "observed_generation_kwh",
]
WIDE_METRICS = [
    "observed_unit_count",
    "calculable_unit_count",
    "reference_capacity_known_kw",
    "missing_output_count",
    "capacity_hold_count",
    "negative_gap_count",
    "reference_gap_partial_sum_kw",
    "zero_reference_capacity_kw",
]
SUM_METRICS = {
    "reference_capacity_known_kw",
    "computed_reference_capacity_kw",
    "reference_gap_partial_sum_kw",
    "zero_reference_capacity_kw",
    "observed_generation_kwh",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def record_spans(raw, scenario):
    """(開始slot, 終了exclusive, 理由)。不明復旧を停止区間へ補完しない。"""
    if scenario not in ("early", "late"):
        raise ValueError("予定端点シナリオが不明")
    try:
        start = datetime.strptime(raw["停止日時"], "%Y/%m/%d %H:%M")
    except ValueError:
        return [(0, HORIZON, INVALID)]  # 当該設備の判定を保留。停止とは扱わない。
    s = (start - EPOCH).total_seconds() / 1800
    low = math.floor(s)
    if low >= HORIZON:
        return []
    if not raw["復旧予定日"] or raw["復旧見通し"] != "あり":
        return [(max(0, low), HORIZON, UNKNOWN)]
    try:
        recovery = datetime.strptime(raw["復旧予定日"], "%Y/%m/%d")
    except ValueError:
        return [(max(0, low), HORIZON, INVALID)]
    day = int((recovery - EPOCH).total_seconds() / 1800)
    if start >= recovery + timedelta(days=1):
        return [(max(0, low), HORIZON, INVALID)]
    end = day + (48 if scenario == "late" else 0)
    spans = [(math.ceil(s), end, FULL), (max(low, day), day + 48, BOUNDARY)]
    if s != low:
        spans.append((low, low + 1, BOUNDARY))
    return [(max(0, a), min(HORIZON, b), f) for a, b, f in spans if min(HORIZON, b) > max(0, a)]


def prepare_records(events):
    contents, originals, by_version = {}, [], {v: {} for v in VERSIONS}
    for e in events:
        raw = e["raw"]
        cid = digest(sorted(raw.items()))  # 全元セル、更新日時も含む完全同一内容。
        if cid not in contents:
            value, failed = None, False
            if raw["低下量"]:
                try:
                    value = Decimal(raw["低下量"])
                    if not value.is_finite():
                        raise InvalidOperation
                except InvalidOperation:
                    failed = True
                    value = None
            contents[cid] = dict(
                content_id=cid,
                **raw,
                published_mean_reduction_kw=value,
                qa_reduction_numeric_parse_failed=failed,
            )
        rid = e["source_sha256"] + ":" + str(e["source_row"])
        originals.append(
            dict(
                snapshot=e["snapshot"],
                record_row_id=rid,
                source_file=e["source_file"],
                source_sha256=e["source_sha256"],
                source_row=e["source_row"],
                content_id=cid,
                candidate_asset_indices=e["candidate_aids"],
                **raw,
            )
        )
        v = by_version[e["snapshot"]]
        if cid not in v:
            v[cid] = dict(
                raw=raw, aids=e["candidate_aids"], row_ids=[], identity=event_identity_candidate(e)
            )
        v[cid]["row_ids"].append(rid)
    for rows in by_version.values():
        groups = defaultdict(set)
        for cid, r in rows.items():
            groups[r["identity"]].add(cid)
        for r in rows.values():
            r["update_candidate"] = len(groups[r["identity"]]) > 1
    attach_update_provenance(contents, originals)
    return contents, originals, by_version


def attach_update_provenance(contents, originals):
    """現在コマ外の更新候補も追跡できる候補群ID。公式event IDではない。"""
    groups = defaultdict(set)
    copies = Counter()
    for cid, row in contents.items():
        row["update_candidate_group_id"] = digest(event_identity_candidate({"raw": row}))
    for row in originals:
        group = contents[row["content_id"]]["update_candidate_group_id"]
        groups[(row["snapshot"], group)].add(row["content_id"])
        copies[(row["snapshot"], row["content_id"])] += 1
    for row in originals:
        row["update_candidate_group_id"] = contents[row["content_id"]]["update_candidate_group_id"]
        row["snapshot_candidate_update_flag"] = (
            len(groups[(row["snapshot"], row["update_candidate_group_id"])]) > 1
        )
        row["snapshot_exact_content_copy_count"] = copies[(row["snapshot"], row["content_id"])]


def preserve_original_extras(root, repo, rows):
    """別読取で全元セルを再検査。無名の末尾空セルも原値として保持。"""
    prior = json.loads((repo / "reports/generation_hjks_FY2025_qa.json").read_text())
    by_version = defaultdict(dict)
    for row in rows:
        by_version[row["snapshot"]][row["source_row"]] = row
    for version in VERSIONS:
        _, original, _ = read_raw(root, prior["snapshots"][version]["source"])
        if len(original) != len(by_version[version]):
            raise ValueError("停止原行数不一致")
        for number, raw, extra in original:
            target = by_version[version][number]
            if any(target[k] != value for k, value in raw.items()):
                raise ValueError("停止元セル不一致")
            target["source_extra_cells_json"] = json.dumps(extra, ensure_ascii=False)


def market_state_columns(grouped):
    wide = grouped.pivot(index=["area", "slot"], columns="primary_state", values=WIDE_METRICS)
    wide = wide.reindex(columns=pd.MultiIndex.from_product([WIDE_METRICS, STATES]))
    wide.columns = [state + "__" + metric for metric, state in wide.columns]
    count_cols = [c for c in wide.columns if c.endswith("count")]
    wide[count_cols] = wide[count_cols].fillna(0).astype("int32")
    return wide.reset_index()


def read_market_profile(path, *, snapshot, scenario):
    """市場joinには版・端点の明示選択を要求し、4重の行増殖を防ぐ。"""
    if snapshot not in VERSIONS or scenario not in ("early", "late"):
        raise ValueError("snapshotと予定端点シナリオの明示選択が必要")
    result = pq.read_table(
        path, filters=[("snapshot", "=", snapshot), ("scenario", "=", scenario)]
    ).to_pandas()
    if result.empty or result.duplicated(["area", "delivery_date", "period"]).any():
        raise ValueError("地域×30分の結合キーが空/非一意")
    return result


def classify_context(members, records):
    """優先順を固定した排他的主区分＋非排他的な根拠。平均量を選ばない。"""
    flags = 0
    for f in members.values():
        flags |= f
    active = [c for c, f in members.items() if f & FULL]
    updates = any(records[c]["update_candidate"] for c in members)
    if flags & INVALID:
        state = "held_invalid_dates"
    elif flags & UNKNOWN:
        state = "held_unknown_recovery"
    elif updates:
        state = "held_update_candidates"
    elif len(members) > 1:
        state = "held_multiple_records"
    elif flags & BOUNDARY:
        state = "held_date_boundary"
    elif active:
        state = STATES[TYPE_STATE.get(records[active[0]]["raw"]["停止区分"], 10)]
    else:
        state = "no_applicable_record"
    match = 1 if active else -1 if flags & (INVALID | UNKNOWN) else 0
    return (
        state,
        match,
        dict(
            has_invalid_dates=bool(flags & INVALID),
            has_unknown_recovery=bool(flags & UNKNOWN),
            has_update_candidates=updates,
            has_multiple_contents=len(members) > 1,
            has_date_boundary=bool(flags & BOUNDARY),
        ),
    )


def sweep_asset(records, scenario, contexts, snapshot, aid):
    changes = defaultdict(lambda: [[], []])
    for cid, r in records.items():
        for start, end, flag in record_spans(r["raw"], scenario):
            changes[start][0].append((cid, flag))
            changes[end][1].append((cid, flag))
    points = sorted(set(changes) | {0, HORIZON})
    state = np.full(HORIZON, STATE_ID["no_applicable_record"], dtype=np.uint8)
    context = np.zeros(HORIZON, dtype=np.int32)
    match = np.zeros(HORIZON, dtype=np.int8)
    current = Counter()
    local_ids = {}
    for left, right in zip(points[:-1], points[1:]):
        for token in changes[left][1]:
            current[token] -= 1
            if current[token] == 0:
                del current[token]
        current.update(changes[left][0])
        if not current:
            continue
        members = defaultdict(int)
        for (cid, flag), count in current.items():
            if count <= 0:
                raise ValueError("公表記録rangeの不整合")
            members[cid] |= flag
        signature = tuple(sorted(members.items()))
        if signature not in local_ids:
            label, found, flags = classify_context(members, records)
            idx = len(contexts)
            contexts.append(
                dict(
                    context_id=idx,
                    snapshot=snapshot,
                    scenario=scenario,
                    asset_index=aid,
                    member_content_ids=list(sorted(members)),
                    member_flags=[members[c] for c in sorted(members)],
                    full_scenario_content_ids=[c for c in sorted(members) if members[c] & FULL],
                    source_row_ids=[rid for c in sorted(members) for rid in records[c]["row_ids"]],
                    distinct_content_count=len(members),
                    original_row_count=sum(len(records[c]["row_ids"]) for c in members),
                    primary_state=label,
                    scenario_match=found,
                    **flags,
                )
            )
            local_ids[signature] = idx
        idx = local_ids[signature]
        row = contexts[idx]
        state[left:right], context[left:right], match[left:right] = (
            STATE_ID[row["primary_state"]],
            idx,
            row["scenario_match"],
        )
    return state, context, match


def annotate_profile(records, mapping, df, scenario, snapshot, contexts):
    by_asset = defaultdict(dict)
    for cid, r in records.items():
        if len(r["aids"]) == 1:
            by_asset[r["aids"][0]][cid] = r
    n = len(mapping)
    state = np.zeros((n, HORIZON), dtype=np.uint8)
    context = np.zeros((n, HORIZON), dtype=np.int32)
    match = np.full((n, HORIZON), -1, dtype=np.int8)
    for aid, a in enumerate(mapping):
        if a["research_use_status"] != "use_diagnostic":
            state[aid] = (
                STATE_ID["held_asset_period"] if "台帳稼働日" in a["research_use_reason"] else 0
            )
            continue
        state[aid], context[aid], match[aid] = sweep_asset(
            by_asset[aid], scenario, contexts, snapshot, aid
        )
    aids, slots = df.aid.to_numpy(), df.slot.to_numpy()
    result = [a[aids, slots] for a in (state, context, match)]
    bad = (df.period_bad | df.boundary).to_numpy() & (result[0] != 0)
    result[0][bad], result[2][bad] = STATE_ID["held_asset_period"], -1
    return tuple(result)


def aggregate_profile(df, state, match):
    d = df[["aid", "slot", "area", "gen", "cap", "gap"]].copy()
    d["state_id"] = state
    d["observed_unit_count"] = 1
    d["calculable_unit_count"] = d.gap.notna().astype("int8")
    d["known_capacity_unit_count"] = d.cap.notna().astype("int8")
    d["missing_output_count"] = d.gen.isna().astype("int8")
    d["capacity_hold_count"] = d.cap.isna().astype("int8")
    d["negative_gap_count"] = d.gap.lt(0).astype("int8")
    d["zero_output_count"] = d.gen.eq(0).astype("int8")
    d["calculable_zero_count"] = (d.gen.eq(0) & d.gap.notna()).astype("int8")
    d["full_scenario_record_unit_count"] = (match == 1).astype("int8")
    d["scenario_match_unknown_unit_count"] = (match == -1).astype("int8")
    d["reference_capacity_known_kw"] = d.cap
    d["computed_reference_capacity_kw"] = d.cap.where(d.gap.notna())
    d["reference_gap_partial_sum_kw"] = d.gap
    d["zero_reference_capacity_kw"] = np.where(d.gen.eq(0) & d.gap.notna(), d.cap, 0.0)
    d["zero_reference_capacity_kw"] = d.zero_reference_capacity_kw.where(d.gap.notna())
    d["observed_generation_kwh"] = d.gen / 2  # 承認済み平均kWの逆変換。原値を変更しない。
    g = (
        d.groupby(["area", "slot", "state_id"], observed=True)[METRICS]
        .sum(min_count=1)
        .reset_index()
    )
    # 主区分の排他性を元の地域×時刻の件数・数値と照合。
    totals = d.groupby(["area", "slot"], observed=True)[METRICS].sum(min_count=1).reset_index()
    recombined = g.groupby(["area", "slot"], observed=True)[METRICS].sum(min_count=1).reset_index()
    for c in METRICS:
        if not np.allclose(totals[c], recombined[c], equal_nan=True, atol=1e-6, rtol=0):
            raise ValueError("主区分の重複/脱落/部分和不一致: " + c)
    return d, g, totals


def add_times(frame):
    f = frame.copy()
    slots = f.slot.astype("int64")
    f["delivery_date"] = [datetime.fromisoformat(time_label(s)).date() for s in slots // 48 * 48]
    f["period"] = slots % 48 + 1
    f["timestamp_start"] = pd.to_datetime(
        slots * 1800, unit="s", utc=True, origin="2025-03-31 15:00:00"
    ).dt.tz_convert("Asia/Tokyo")
    f["timestamp_end"] = f.timestamp_start + pd.Timedelta(minutes=30)
    f["timezone"] = "Asia/Tokyo"
    f["aggregation_scope"] = "対象設備の部分和（地域全体・競争的供給者全体ではない）"
    return f


def summarize_states(d, mapping, snapshot, scenario):
    results = []
    month_by_day = [(EPOCH + timedelta(days=i)).strftime("%Y-%m") for i in range(365)]
    dimensions = [
        ("全国", np.full(len(d), "全国")),
        ("エリア", d.area),
        ("月", pd.Categorical(np.array(month_by_day)[d.slot.to_numpy() // 48])),
    ]
    for dimension, labels in dimensions:
        temp = d.assign(group=labels)
        grouped = temp.groupby(["group", "state_id"], observed=True)
        sums = grouped[METRICS].sum(min_count=1)
        units = grouped.aid.nunique()
        for key, r in sums.iterrows():
            group, sid = key
            results.append(
                dict(
                    snapshot=snapshot,
                    scenario=scenario,
                    dimension=dimension,
                    group=str(group),
                    primary_state=STATES[int(sid)],
                    state_japanese=STATE_LABELS[int(sid)],
                    distinct_asset_labels=int(units.loc[key]),
                    **{
                        k: float(v)
                        if k in SUM_METRICS and pd.notna(v)
                        else None
                        if pd.isna(v)
                        else int(v)
                        for k, v in r.items()
                    },
                    sum_note="容量/差の期間合計はkW・コマ。物理設備数・電力量と混同しない",
                )
            )
    return results


def write_annotated(source, output, mapping, df, annotations):
    writer, offset = None, 0
    ids = pa.array([r["asset_key_candidate"] for r in mapping])
    for b in pq.ParquetFile(source).iter_batches(batch_size=65536):
        t = pa.Table.from_batches([b])
        aid = pc.index_in(t["asset_key_candidate"], value_set=ids).to_numpy()
        slots = np.array(
            [
                (d - EPOCH.date()).days * 48 + int(p) - 1
                for d, p in zip(t["delivery_date"].to_pylist(), t["period"].to_pylist())
            ]
        )
        sl = slice(offset, offset + len(t))
        if not np.array_equal(aid, df.aid.to_numpy()[sl]) or not np.array_equal(
            slots, df.slot.to_numpy()[sl]
        ):
            raise ValueError("固定版と状態付与の行順不一致")
        for profile, (states, contexts, match) in annotations.items():
            t = t.append_column(
                "pubrec_" + profile + "_state", pc.take(pa.array(STATES), pa.array(states[sl]))
            )
            t = t.append_column("pubrec_" + profile + "_context_id", pa.array(contexts[sl]))
            t = t.append_column(
                "pubrec_" + profile + "_has_full_scenario_record",
                pa.array(match[sl] == 1, mask=match[sl] < 0),
            )
        if writer is None:
            meta = dict(t.schema.metadata or {})
            meta[b"public_record_scope"] = (
                "snapshot収録記録の診断。実停止・市場時点・最終headroomではない".encode()
            )
            writer = pq.ParquetWriter(output, t.schema.with_metadata(meta), compression="zstd")
        writer.write_table(t)
        offset += len(t)
    writer.close()
    if offset != len(df):
        raise ValueError("行脱落")
    # 独立した再読込比較。全51元列の全セル・型・元順序を保持。
    original = pq.ParquetFile(source)
    derived = pq.ParquetFile(output)
    for a, b in zip(
        original.iter_batches(batch_size=65536),
        derived.iter_batches(columns=original.schema_arrow.names, batch_size=65536),
        strict=True,
    ):
        if not pa.Table.from_batches([a]).equals(pa.Table.from_batches([b]), check_metadata=False):
            raise ValueError("元列の全セル照合不一致")
    return offset


def run(root, repo, output):
    frozen = check_frozen(root, repo)
    old = json.loads(
        (repo / "reports/reference_gap_interpretation_hash_audit_20261016.json").read_text()
    )
    for r in old["outputs"]:
        if sha256(root / r["path"]) != r["sha256"]:
            raise ValueError("直前診断版のSHA不一致")
    output.mkdir(parents=True, exist_ok=False)
    mapping = load_csv(repo / "reports/reference_gap_analysis_mapping_20261016.csv")
    thin = (
        root
        / "data/interim/occto/reference_gap_interpretation/20261016/results"
        / "observed_cells_diagnostic.parquet"
    )
    df = pd.read_parquet(thin)
    if df.duplicated(["aid", "slot"]).any():
        raise ValueError("元観測key重複")
    events = load_event_candidates(root, repo, mapping)
    contents, original_rows, versions = prepare_records(events)
    preserve_original_extras(root, repo, original_rows)
    pq.write_table(
        pa.Table.from_pylist(original_rows),
        output / "snapshot_original_record_rows.parquet",
        compression="zstd",
    )
    if not pa.Table.from_pylist(original_rows).equals(
        pq.read_table(output / "snapshot_original_record_rows.parquet")
    ):
        raise ValueError("停止元行のParquet再読込不一致")
    pq.write_table(
        pa.Table.from_pylist(list(contents.values())),
        output / "record_contents.parquet",
        compression="zstd",
    )
    contexts = [
        dict(
            context_id=0,
            snapshot="",
            scenario="",
            asset_index=-1,
            member_content_ids=[],
            member_flags=[],
            full_scenario_content_ids=[],
            source_row_ids=[],
            distinct_content_count=0,
            original_row_count=0,
            primary_state="empty_context",
            scenario_match=0,
            has_invalid_dates=False,
            has_unknown_recovery=False,
            has_update_candidates=False,
            has_multiple_contents=False,
            has_date_boundary=False,
        )
    ]
    annotations, region_parts, market_parts, summaries, profile_qa = {}, [], [], [], []
    for snapshot in VERSIONS:
        for scenario in ("early", "late"):
            profile = snapshot.rsplit("_", 1)[-1] + "_" + scenario
            states, ctx, match = annotate_profile(
                versions[snapshot], mapping, df, scenario, snapshot, contexts
            )
            annotations[profile] = states, ctx, match
            d, grouped, totals = aggregate_profile(df, states, match)
            summaries.extend(summarize_states(d, mapping, snapshot, scenario))
            grouped["primary_state"] = grouped.state_id.map(dict(enumerate(STATES)))
            grouped["snapshot"], grouped["scenario"] = snapshot, scenario
            region_parts.append(add_times(grouped))
            wide = market_state_columns(grouped)
            totals = totals.merge(wide, on=["area", "slot"], how="left", validate="one_to_one")
            totals["snapshot"], totals["scenario"] = snapshot, scenario
            market_parts.append(add_times(totals))
            counts = Counter(states.tolist())
            profile_qa.append(
                dict(
                    snapshot=snapshot,
                    scenario=scenario,
                    original_observations=len(df),
                    annotated_observations=len(states),
                    state_counts={STATES[k]: v for k, v in sorted(counts.items())},
                    unique_asset_period_cells=len(df),
                    scenario_full_record_cells=int((match == 1).sum()),
                    scenario_match_unknown_cells=int((match < 0).sum()),
                    zero_reference_capacity_kw_cell_sum=float(
                        d.zero_reference_capacity_kw.sum(min_count=1)
                    ),
                )
            )
            print(profile, profile_qa[-1]["state_counts"], flush=True)
    source = (
        root
        / "data/interim/occto/reference_capacity_gap/20261016_diagnostic_v1"
        / "reference_capacity_gap_FY2025.parquet"
    )
    rows = write_annotated(
        source,
        output / "generation_with_snapshot_public_records_FY2025.parquet",
        mapping,
        df,
        annotations,
    )
    pq.write_table(
        pa.Table.from_pylist(contexts),
        output / "public_record_contexts.parquet",
        compression="zstd",
    )
    regions = pd.concat(region_parts, ignore_index=True)
    markets = pd.concat(market_parts, ignore_index=True)
    keys = ["snapshot", "scenario", "area", "delivery_date", "period"]
    if markets.duplicated(keys).any() or regions.duplicated(keys + ["primary_state"]).any():
        raise ValueError("地域派生表key重複")
    for _, g in markets.groupby(["snapshot", "scenario", "area"], observed=True):
        if (
            len(g) != HORIZON
            or g.period.min() != 1
            or g.period.max() != 48
            or g.delivery_date.nunique() != 365
        ):
            raise ValueError("地域派生表365日48コマ不一致")
    pq.write_table(
        pa.Table.from_pandas(regions, preserve_index=False),
        output / "regional_state_partial_sums.parquet",
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pandas(markets, preserve_index=False),
        output / "market_join_by_snapshot_scenario.parquet",
        compression="zstd",
    )
    write_csv(repo / "reports/public_record_state_summary_20261016.csv", summaries)
    differences = []
    pairs = [
        ("58852_early", "58852_late"),
        ("7828_early", "7828_late"),
        ("58852_early", "7828_early"),
        ("58852_late", "7828_late"),
    ]
    for left, right in pairs:
        ls, _, lm = annotations[left]
        rs, _, rm = annotations[right]
        for dimension, labels in [("全国", np.full(len(df), "全国")), ("エリア", df.area)]:
            d = pd.DataFrame(
                dict(group=labels, left_state=ls, right_state=rs, left_match=lm, right_match=rm)
            )
            d["gen_kwh"] = df.gen.to_numpy() / 2
            for (group, left_state, right_state), g in d.groupby(
                ["group", "left_state", "right_state"], observed=True
            ):
                differences.append(
                    dict(
                        left_profile=left,
                        right_profile=right,
                        dimension=dimension,
                        group=str(group),
                        left_state=STATES[int(left_state)],
                        right_state=STATES[int(right_state)],
                        cells=len(g),
                        full_scenario_flag_changed=int((g.left_match != g.right_match).sum()),
                        observed_generation_kwh=float(g.gen_kwh.sum(min_count=1))
                        if g.gen_kwh.notna().any()
                        else None,
                    )
                )
    write_csv(repo / "reports/public_record_profile_comparison_20261016.csv", differences)
    check_frozen(root, repo)
    for r in old["outputs"]:
        if sha256(root / r["path"]) != r["sha256"]:
            raise ValueError("直前診断版が変更された")
    both = set(versions[VERSIONS[0]]) & set(versions[VERSIONS[1]])
    qa = dict(
        diagnostic_version="20261016_A_v1",
        annotation_rate=1.0,
        original_rows=rows,
        original_columns=51,
        original_columns_all_cells_equal=True,
        profile_qa=profile_qa,
        duplicate_key=0,
        dropped_rows=0,
        original_record_rows=len(original_rows),
        public_record_rows_roundtrip_equal=True,
        distinct_full_content_count=len(contents),
        shared_exact_contents_between_snapshots=len(both),
        context_count=len(contexts),
        source_record_mapping={
            v: dict(
                original_rows=sum(len(r["row_ids"]) for r in rs.values()),
                distinct_contents=len(rs),
                unique_mapping_contents=sum(len(r["aids"]) == 1 for r in rs.values()),
                update_candidate_contents=sum(r["update_candidate"] for r in rs.values()),
                exact_copy_extra_rows=sum(len(r["row_ids"]) - 1 for r in rs.values()),
            )
            for v, rs in versions.items()
        },
        record_reduction_parse_failures=sum(
            r["qa_reduction_numeric_parse_failed"] for r in contents.values()
        ),
        market_join_rows=len(markets),
        regional_state_rows=len(regions),
        regional_partition_reconciled=True,
        baseline_adopted=False,
        numerical_loss_deduction=False,
        output=str(output),
    )
    qa.update(validate_outputs(root, output))
    outputs = [
        dict(path=str(p.relative_to(root)), size=p.stat().st_size, sha256=sha256(p))
        for p in sorted(output.glob("*.parquet"))
    ]
    manifest = dict(
        base_commit="8941150b5da991dc570d0afb137fae68873a11e5",
        version="20261016_A_v1",
        previous_fixed_manifest="metadata/research/reference_gap_frozen_version_20261016.json",
        previous_interpretation_outputs=old["outputs"],
        frozen_base_files=frozen["files"],
        outputs=outputs,
        raw_modified=False,
        baseline_adopted=False,
    )
    (repo / "metadata/research/public_record_annotation_frozen_20261016.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    (repo / "reports/public_record_annotation_qa_20261016.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n"
    )
    return qa


def validate_outputs(root, output):
    """原停止全セル、context参照、既存地域差との一致を検査する。"""
    panel = pq.ParquetFile(output / "generation_with_snapshot_public_records_FY2025.parquet")
    nulls = {
        panel.schema_arrow.names[c]: sum(
            panel.metadata.row_group(i).column(c).statistics.null_count
            for i in range(panel.num_row_groups)
        )
        for c in range(len(panel.schema_arrow.names))
    }
    originals = pq.read_table(output / "snapshot_original_record_rows.parquet").to_pylist()
    original_before = digest(originals)
    # 読取結果を原CSVと独立照合。末尾無名空セルも検査する。
    repo = Path(__file__).resolve().parents[1]
    preserve_original_extras(root, repo, originals)
    if digest(originals) != original_before:
        raise ValueError("停止原行/無名セルが変更/欠落")
    context_table = pq.read_table(output / "public_record_contexts.parquet")
    contexts = context_table.to_pylist()
    row_ids = {r["record_row_id"] for r in originals}
    content_ids = {r["content_id"] for r in originals}
    if any(
        not set(r["source_row_ids"]) <= row_ids or not set(r["member_content_ids"]) <= content_ids
        for r in contexts
    ):
        raise ValueError("contextから停止原行を追跡できない")
    old = pq.read_table(
        root
        / "data/interim/occto/reference_capacity_gap/20261016_diagnostic_v1"
        / "reference_gap_regional_partial_sums_FY2025.parquet"
    ).to_pandas()
    old = old[old.scenario.eq("observed_calculation_scope")]
    reason_counts = []
    for snapshot in VERSIONS:
        for scenario in ("early", "late"):
            profile = snapshot.rsplit("_", 1)[-1] + "_" + scenario
            frame = read_market_profile(
                output / "market_join_by_snapshot_scenario.parquet",
                snapshot=snapshot,
                scenario=scenario,
            )
            comp = frame.merge(
                old,
                on=["area", "delivery_date", "period"],
                validate="one_to_one",
                suffixes=("_new", "_old"),
            )
            if len(comp) != 175200 or not np.allclose(
                comp.reference_gap_partial_sum_kw_new,
                comp.reference_gap_partial_sum_kw_old.astype(float),
                atol=0,
                rtol=0,
                equal_nan=True,
            ):
                raise ValueError("固定版地域部分和との不一致")
            refs = panel.read(columns=["pubrec_" + profile + "_context_id"]).to_pandas()
            indices = refs["pubrec_" + profile + "_context_id"].to_numpy()
            if indices.min() < 0 or indices.max() >= len(contexts):
                raise ValueError("context参照範囲外")
            ctx_snapshot = np.array([r["snapshot"] for r in contexts])
            ctx_scenario = np.array([r["scenario"] for r in contexts])
            nonempty = indices != 0
            if not np.all(ctx_snapshot[indices[nonempty]] == snapshot) or not np.all(
                ctx_scenario[indices[nonempty]] == scenario
            ):
                raise ValueError("snapshot/端点が混在")
            reasons = {
                key: int(np.array([r[key] for r in contexts])[indices].sum())
                for key in (
                    "has_invalid_dates",
                    "has_unknown_recovery",
                    "has_update_candidates",
                    "has_multiple_contents",
                    "has_date_boundary",
                )
            }
            reason_counts.append(dict(snapshot=snapshot, scenario=scenario, **reasons))
    return dict(
        null_counts=nulls,
        original_event_rows_all_cells_equal=len(originals),
        original_extra_cells_preserved=True,
        context_referential_integrity=True,
        fixed_regional_gap_equal_all_four_profiles=True,
        nonexclusive_context_reason_counts=reason_counts,
    )
