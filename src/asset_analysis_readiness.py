"""設備対応の利用診断・別シナリオ比較。容量適用・停止展開・headroomは行わない。"""

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.generation_hjks import asset_id, normalize_name, read_raw
from src.time_normalization import fiscal_dates, sha256
from src.unmatched_generation import plant_stem, unit_token, write_csv

NO_CONFLICT = "現時点で対応上の矛盾は見つからない"
CONDITIONAL = "plant等への集約条件付きで利用を検討できる"
HOLD = "候補の競合・設備範囲・期間等の理由で保留"
# 既存の公式設備表の読取結果。公表日と設備対象時点を区別する。
DOCUMENT_TIMES = {
    "GB583": ("URL名2024。公表日は未確認", "設備表の運転開始1985〜2010年"),
    "GB573": ("URL名2024。公表日は未確認", "設備表の運転開始2007〜2016年"),
    "GB593": ("PDFに公表日未確認", "設備表の運転開始1998〜2014年"),
    "GB4Q3": ("URL名2026年3月。公表日は未確認", "新1〜3号の運転開始2023年"),
    "GB553": ("URL名2024。公表日は未確認", "1・2号の運転開始2003・2013年"),
    "GAZT3": ("URL名2026年9月。公表日は未確認", "更新後1・2号の運転開始2024年"),
    "GC5F3": ("PDFはURL名2026年9月。別の公式発表は2025-03-03", "3号の運転開始2025-03-01"),
    "48193": ("URL名2024。公表日は未確認", "7-2号の運転開始2018年3月"),
    "GV943": ("現行Webの公表日は未確認", "営業運転開始2021-01-08。HJKS開始日は2021-01-07"),
    "GAWA3": ("URL名2024。公表日は未確認", "更新後1号の運転開始2023年6月"),
}


def load_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def target_key(row):
    return (row["エリア"], row["発電所コード"], normalize_name(row["ユニット名"]))


def diagnose_candidates(assets, evidence, master):
    by_source, by_target = defaultdict(set), defaultdict(set)
    strict_targets = defaultdict(set)
    for a in assets:
        if a["unit_match"] == "True":
            for target in a["candidate_snapshot_rows"].split("|"):
                strict_targets[target].add(a["asset_key_candidate"])
    for e in evidence:
        by_source[e["asset_key_candidate"]].add(e["hjks_snapshot_row_id"])
        by_target[e["hjks_snapshot_row_id"]].add(e["asset_key_candidate"])
    diagnosed = []
    for e in evidence:
        aid, target = e["asset_key_candidate"], e["hjks_snapshot_row_id"]
        source_conflict = len(by_source[aid]) != 1
        target_conflict = len(by_target[target] | strict_targets[target]) > 1
        code = e["generation_plant_code"]
        row = master[target]
        state = (
            HOLD
            if source_conflict or target_conflict
            else (CONDITIONAL if code == "48193" else NO_CONFLICT)
        )
        publication, equipment_time = DOCUMENT_TIMES[code]
        notes = "号機/系列/軸を既存設備表と照合。計測境界とFY容量履歴は別途未確認"
        if code == "48193":
            notes = "7-2 CCブロック候補。4B193のGT A/B/Cと重ねて容量加算しない。STを含む範囲を維持"
        if code == "GV943":
            notes += "。開始日の1日差は登録開始と営業運開の意味差が未確認。FY開始前の存在は双方支持"
        diagnosed.append(
            {
                **e,
                "analysis_use_diagnosis": state,
                "source_candidate_count": len(by_source[aid]),
                "target_reused_by_source_count": len(by_target[target] | strict_targets[target]),
                "equipment_scope_check": "ブロック/構成機器の条件あり"
                if code == "48193"
                else "既存公式設備表と整合",
                "old_new_equipment_check": "新/旧識別を保持。旧設備への置換なし",
                "document_publication_time_evidence": publication,
                "equipment_reference_time_evidence": equipment_time,
                "fy2025_identity_support": "運開がFY開始前で存在を支持。期間全体の不変性は未証明",
                "hjks_operation_start_raw": row["稼働開始日"],
                "hjks_operation_end_raw": row["稼働終了日"],
                "fy_capacity_validity_verified": False,
                "metering_boundary_verified": False,
                "baseline_adopted": False,
                "analysis_notes": notes,
            }
        )
    return diagnosed


def build_scenario(assets, diagnosis, include_candidates):
    """全元assetを1回保持し、候補集合はJSON列へ保存。多対多に展開しない。"""
    extra = defaultdict(set)
    proposed = defaultdict(set)
    for e in diagnosis:
        proposed[e["asset_key_candidate"]].add(e["hjks_snapshot_row_id"])
        if include_candidates and e["analysis_use_diagnosis"] != HOLD:
            extra[e["asset_key_candidate"]].add(e["hjks_snapshot_row_id"])
    groups = defaultdict(list)
    for a in assets:
        groups[(a["area"], a["plant_code"], a["plant_name_normalized"])].append(a)
    plant_add = {
        a["asset_key_candidate"]
        for g in groups.values()
        if all(len(extra[a["asset_key_candidate"]]) == 1 for a in g)
        for a in g
    }
    states = defaultdict(set)
    for e in diagnosis:
        states[e["asset_key_candidate"]].add(e["analysis_use_diagnosis"])
    output = []
    for a in assets:
        aid = a["asset_key_candidate"]
        strict = set(filter(None, a["candidate_snapshot_rows"].split("|")))
        selected = strict if a["unit_match"] == "True" else extra[aid]
        output.append(
            {
                "asset_key_candidate": aid,
                "scenario": "candidate49_diagnostic" if include_candidates else "strict_existing",
                "plant_matched": a["plant_match"] == "True" or aid in plant_add,
                "unit_matched": a["unit_match"] == "True" or len(extra[aid]) == 1,
                "selected_unit_target_ids_json": json.dumps(sorted(selected), ensure_ascii=False),
                "proposed_candidate_ids_json": json.dumps(
                    sorted(proposed[aid]), ensure_ascii=False
                ),
                "strict_plant_pool_ids_json": json.dumps(
                    a["plant_candidate_snapshot_rows"].split("|"), ensure_ascii=False
                ),
                "mapping_route": "strict_unit"
                if a["unit_match"] == "True"
                else ("strongly_supported_candidate" if extra[aid] else "unmatched_or_ambiguous"),
                "analysis_use_diagnosis": "|".join(sorted(states[aid]))
                if extra[aid]
                else "既存strict判定。物理範囲は保証しない",
                "held_candidate": HOLD in states[aid],
                "candidate_target_count": len(proposed[aid]),
                "baseline_adopted": False,
            }
        )
    if len({r["asset_key_candidate"] for r in output}) != len(assets):
        raise ValueError("元asset重複。診断結合を停止")
    return output


def gap_diagnosis(asset, pool):
    """plantだけ一致する量の理由候補。意味的な正式対応には使用しない。"""
    token = unit_token(asset["unit_name"])
    if asset["unit_candidate_count"] != "0":
        return "対応情報の不足", "同名正規化unitが複数。容量等の異なる台帳行を選べない"
    same = [
        r
        for r in pool
        if token
        and unit_token(r["ユニット名"]) == token
        and plant_stem(r["発電所名"]) == plant_stem(asset["plant_name"])
    ]
    if len(same) == 1:
        return "表記の違い", "同code/area内で名称基部と号機番号が合う候補。正式対応は追加しない"
    components = any(re.search(r"GT|ST", normalize_name(r["ユニット名"]), re.I) for r in pool)
    if components or asset["unit_name"] in ("第一発電所", "第二発電所", ""):
        return (
            "設備階層や集約範囲の違い",
            "ブロック/GT/STまたは複数号機集約の可能性。合計容量は未確定",
        )
    if token and all(unit_token(r["ユニット名"]) != token for r in pool):
        return "対応情報の不足", "strict plantのcodeは一意でも号機/系列番号が対象unitと合わない"
    return "その他", "名称・識別範囲をこの資料だけで説明できない"


def left_join_diagnostics(days, scenario):
    left = pd.DataFrame(days)
    right = pd.DataFrame(scenario)
    joined = left.merge(
        right, on="asset_key_candidate", how="left", validate="many_to_one", indicator=True
    )
    if len(joined) != len(left) or not (joined["_merge"] == "both").all():
        raise ValueError("行増殖または元assetの脱落")
    if joined["source_day_key"].duplicated().any():
        raise ValueError("unit日キー重複")
    if joined["observed_kwh"].tolist() != left["observed_kwh"].tolist():
        raise ValueError("診断left joinで原数値観測量または行順が変化")
    joined = joined.drop(columns="_merge")
    return joined.to_dict("records")


def compare_coverage(joined):
    groups = defaultdict(list)
    for r in joined:
        for dim, group in (
            ("全国", "全国"),
            ("エリア", r["area"]),
            ("月", r["month"]),
            ("エリア月", r["area"] + "_" + r["month"]),
        ):
            groups[(r["scenario"], dim, group)].append(r)
    result = []
    for (scenario, dimension, group), rows in sorted(groups.items()):
        total = sum((r["observed_kwh"] for r in rows if r["observed_kwh"] is not None), Decimal(0))
        for level in ("plant", "unit"):
            matched = [r for r in rows if r[level + "_matched"]]
            amount = sum(
                (r["observed_kwh"] for r in matched if r["observed_kwh"] is not None), Decimal(0)
            )
            result.append(
                dict(
                    scenario=scenario,
                    dimension=dimension,
                    group=group,
                    level=level,
                    total_observed_kwh=str(total),
                    matched_observed_kwh=str(amount),
                    unmatched_observed_kwh=str(total - amount),
                    generation_coverage=str(amount / total) if total else None,
                    source_unit_days=len(rows),
                    matched_unit_days=len(matched),
                    matched_day_row_rate=len(matched) / len(rows),
                    null_cell_count=sum(r["null_cells"] for r in rows),
                    unmatched_null_cells=sum(
                        r["null_cells"] for r in rows if not r[level + "_matched"]
                    ),
                )
            )
    return result


def capacity_diagnostics(master, scenarios):
    results = []
    all_ids = set(master)
    for name, scenario in {"677_snapshot_all_rows": None, **scenarios}.items():
        ids = (
            all_ids
            if scenario is None
            else {
                i
                for r in scenario
                if r["unit_matched"]
                for i in json.loads(r["selected_unit_target_ids_json"])
            }
        )
        rows = [master[i] for i in ids]
        values = [Decimal(r["認可出力"]) for r in rows if r["認可出力"] != ""]
        results.append(
            dict(
                scope=name,
                distinct_snapshot_row_count=len(rows),
                authorized_capacity_kw_snapshot_row_sum=str(sum(values, Decimal(0))),
                nonmissing_capacity_rows=len(values),
                zero_capacity_rows=sum(v == 0 for v in values),
                effective_date_present_rows=sum(bool(r["適用開始日"]) for r in rows),
                changed_capacity_present_rows=sum(bool(r["認可出力（変更後）"]) for r in rows),
                operation_start_1900_rows=sum(r["稼働開始日"] == "1900/01/01" for r in rows),
                fy2025_capacity_validity_verified_rows=0,
                fy2025_capacity_denominator=None,
                physical_capacity_coverage=None,
                note="行IDを一度だけ計数した参考台帳値。終了設備・構成機器等を含み物理容量合計ではない",
            )
        )
    return results


def outage_diagnostics(root, prior, master, scenarios):
    master_index = defaultdict(set)
    for i, r in master.items():
        master_index[target_key(r)].add(i)
    results, asset_counts = [], []
    for version in ("hjks_outage_58852", "hjks_outage_7828"):
        snapshot = prior["snapshots"][version]
        _, raw, _ = read_raw(root, snapshot["source"])
        matched = Counter()
        ambiguity = unresolved = 0
        unit_events = defaultdict(list)
        potential, post_fy_update, update_after_start = 0, 0, 0
        event_rows = []
        for number, r, _ in raw:
            key = target_key(r)
            n = len(master_index[key])
            ambiguity += n > 1
            unresolved += n == 0
            matched["unique_master_rows"] += n == 1
            start = r["停止日時"][:10].replace("/", "-")
            recovery = r["復旧予定日"].replace("/", "-")
            # 復旧日は予定・日粒度。包含可能性の棚卸しだけでinterval判定ではない。
            possible = (
                "2025-04-01" <= start <= "2026-03-31"
                or start < "2025-04-01"
                and (not recovery or recovery >= "2025-04-01")
            )
            potential += possible
            update = r["最終更新日時"].replace("/", "-")
            post_fy_update += update[:10] > "2026-03-31"
            update_after_start += update > r["停止日時"].replace("/", "-")
            unit_events[key].append((number, possible))
            event_rows.append((key, possible))
        per_scenario = {}
        for name, scenario in scenarios.items():
            keys = set()
            for a in scenario:
                ids = json.loads(a["selected_unit_target_ids_json"]) if a["unit_matched"] else []
                keyset = {target_key(master[i]) for i in ids}
                keys.update(keyset)
                events = {n for k in keyset for n, _ in unit_events[k]}
                possible_events = {n for k in keyset for n, flag in unit_events[k] if flag}
                asset_counts.append(
                    dict(
                        version=version,
                        scenario=name,
                        asset_key_candidate=a["asset_key_candidate"],
                        observed_event_rows=len(events),
                        possible_fy_event_rows=len(possible_events),
                        no_event_means_no_outage=False,
                    )
                )
            per_scenario[name] = dict(
                event_rows_with_target_key=sum(k in keys for k, _ in event_rows),
                possible_fy_rows_with_target_key=sum(flag and k in keys for k, flag in event_rows),
                target_unit_key_count=len(keys),
            )
        results.append(
            dict(
                version=version,
                rows=len(raw),
                uniquely_linkable_to_677_rows=matched["unique_master_rows"],
                unique_master_match_rate=matched["unique_master_rows"] / len(raw),
                ambiguous_to_677_rows=ambiguity,
                unmatched_to_677_rows=unresolved,
                potential_fy_overlap_rows=potential,
                updated_after_fy_rows=post_fy_update,
                updated_after_start_rows=update_after_start,
                actual_recovery_field_available=False,
                initial_publication_field_available=False,
                expected_recovery_time_available=False,
                as_of_complete_history_reconstructible=False,
                strict_vs_candidate=per_scenario,
                existing_qa={
                    k: snapshot[k]
                    for k in (
                        "candidate_event_duplicate_groups",
                        "candidate_event_duplicate_excess_rows",
                        "candidate_update_groups",
                        "field_ranges",
                        "start_before_fy_recovery_in_or_after_fy",
                        "start_before_fy_recovery_unknown",
                    )
                },
                duplicate_rows_removed=0,
                interval_expansion_performed=False,
            )
        )
    return results, asset_counts


def run(root: Path, repo: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    reports = repo / "reports"
    files = [
        "generation_hjks_677_mapping_candidates.csv",
        "generation_hjks_FY2025_qa.json",
        "top10_official_mapping_evidence_20261016.csv",
        "top10_official_review_qa_20261016.json",
    ]
    before = {f: sha256(reports / f) for f in files}
    prior = json.loads((reports / files[1]).read_text())
    assets = load_csv(reports / files[0])
    evidence = load_csv(reports / files[2])
    snapshot = prior["snapshots"]["hjks_unit_677"]["source"]
    _, raw, _ = read_raw(root, snapshot)
    master = {f"{snapshot['sha256']}:{n}": r for n, r, _ in raw}
    diagnosis = diagnose_candidates(assets, evidence, master)
    write_csv(reports / "asset_candidate_analysis_use_20261016.csv", diagnosis)
    scenarios = {
        name: build_scenario(assets, diagnosis, include)
        for name, include in (("strict_existing", False), ("candidate49_diagnostic", True))
    }
    asset_index = {a["asset_key_candidate"]: a for a in assets}
    gaps = []
    for a in assets:
        if a["plant_match"] == "True" and a["unit_match"] != "True":
            pool = [master[i] for i in a["plant_candidate_snapshot_rows"].split("|")]
            category, reason = gap_diagnosis(a, pool)
            gaps.append(
                dict(
                    asset_key_candidate=a["asset_key_candidate"],
                    area=a["area"],
                    plant_code=a["plant_code"],
                    plant_name=a["plant_name"],
                    unit_name=a["unit_name"],
                    observed_generation_kwh=a["generation_sum"],
                    gap_category=category,
                    reason=reason,
                    strict_pool_names_json=json.dumps(
                        [(r["発電所名"], r["ユニット名"], r["認可出力"]) for r in pool],
                        ensure_ascii=False,
                    ),
                    final_mapping_applied=False,
                )
            )
    write_csv(reports / "plant_unit_coverage_gap_20261016.csv", gaps)
    days = []
    source_amounts = defaultdict(Decimal)
    seen = set()
    for source in prior["generation"]["inputs"]:
        headers, rows, _ = read_raw(
            root, {"local_path": source["path"], "sha256": source["sha256"]}
        )
        columns = [h for h in headers if re.fullmatch(r"\d\d:\d\d\[kWh\]", h)]
        if len(columns) != 48:
            raise ValueError("48コマでない入力")
        for number, r, _ in rows:
            aid = asset_id(r)
            day = r["対象日"].replace("/", "-")
            # 原ファイルはISO日付。勝手な年度/期間の補完をしない。
            day = date.fromisoformat(day).isoformat()
            key = aid + ":" + day
            if key in seen or aid not in asset_index:
                raise ValueError("元unit日重複または未登録asset")
            seen.add(key)
            values = [Decimal(r[c]) for c in columns if r[c] != ""]
            if any(not v.is_finite() for v in values):
                raise ValueError("非有限値")
            source_amounts[aid] += sum(values, Decimal(0))
            days.append(
                dict(
                    asset_key_candidate=aid,
                    source_day_key=key,
                    source_file=source["path"],
                    source_row=number,
                    delivery_date=day,
                    area=r["エリア"],
                    month=day[:7],
                    observed_kwh=sum(values, Decimal(0)) if values else None,
                    observed_cells=len(values),
                    null_cells=48 - len(values),
                )
            )
    observed_days = {r["delivery_date"] for r in days}
    if observed_days != set(map(str, fiscal_dates(2025))):
        raise ValueError("FY2025の日付集合が不一致")
    if sum(r["null_cells"] for r in days) != prior["generation"]["null_count"]:
        raise ValueError("空セル数が既存QAと不一致")
    combined = []
    joined_qa = {}
    for name, scenario in scenarios.items():
        joined = left_join_diagnostics(days, scenario)
        combined.extend(joined)
        table = pa.Table.from_pylist(joined)
        pq.write_table(table, output / (name + "_unit_day.parquet"), compression="zstd")
        if not pq.read_table(output / (name + "_unit_day.parquet")).equals(table):
            raise ValueError("診断Parquet再読込不一致")
        joined_qa[name] = dict(
            parquet_roundtrip_equal=True,
            input_unit_days=len(days),
            output_unit_days=len(joined),
            represented_30min_rows=len(joined) * 48,
            unintended_row_growth=0,
            silent_row_drop=0,
            duplicate_unit_day_keys=0,
            unmatched_unit_days=sum(not r["unit_matched"] for r in joined),
            unmatched_plant_unit_days=sum(not r["plant_matched"] for r in joined),
        )
    comparison = compare_coverage(combined)
    denominators = defaultdict(set)
    for r in comparison:
        denominators[(r["dimension"], r["group"])].add(r["total_observed_kwh"])
    if any(len(values) != 1 for values in denominators.values()):
        raise ValueError("シナリオ間で分母が不一致")
    write_csv(reports / "asset_scenario_coverage_20261016.csv", comparison)
    for a in assets:
        if source_amounts[a["asset_key_candidate"]] != Decimal(a["generation_sum"]):
            raise ValueError("asset別原観測量不一致")
    capacities = capacity_diagnostics(master, scenarios)
    cap_assets = []
    for name, scenario in scenarios.items():
        for a in scenario:
            ids = json.loads(a["selected_unit_target_ids_json"]) if a["unit_matched"] else []
            cap_assets.append(
                dict(
                    scenario=name,
                    asset_key_candidate=a["asset_key_candidate"],
                    target_row_count=len(ids),
                    target_capacity_raw_json=json.dumps(
                        [master[i]["認可出力"] for i in ids], ensure_ascii=False
                    ),
                    target_effective_date_raw_json=json.dumps(
                        [master[i]["適用開始日"] for i in ids], ensure_ascii=False
                    ),
                    source_capacity_unit="kW",
                    fy_validity_verified=False,
                    metering_boundary_verified=False,
                    capacity_applied_to_generation=False,
                )
            )
    write_csv(reports / "asset_capacity_readiness_20261016.csv", cap_assets)
    unit529 = prior["snapshots"]["hjks_unit_529"]
    capacities.insert(
        0,
        dict(
            capacities[0],
            scope="529_snapshot_all_rows",
            distinct_snapshot_row_count=unit529["rows"],
            authorized_capacity_kw_snapshot_row_sum=unit529["authorized_capacity_kw"][
                "sum_snapshot_only"
            ],
            nonmissing_capacity_rows=unit529["rows"],
            zero_capacity_rows=unit529["authorized_capacity_kw"]["zero_count"],
            operation_start_1900_rows=None,
        ),
    )
    write_csv(reports / "capacity_mapping_readiness_20261016.csv", capacities)
    outages, asset_events = outage_diagnostics(root, prior, master, scenarios)
    write_csv(reports / "outage_asset_link_diagnostics_20261016.csv", asset_events)
    scenario_rows = [r for rows in scenarios.values() for r in rows]
    write_csv(reports / "asset_mapping_scenarios_20261016.csv", scenario_rows)
    gap_summary = {}
    for category in ("表記の違い", "設備階層や集約範囲の違い", "対応情報の不足", "その他"):
        subset = [r for r in gaps if r["gap_category"] == category]
        gap_summary[category] = dict(
            unit_count=len(subset),
            observed_kwh=str(
                sum((Decimal(r["observed_generation_kwh"]) for r in subset), Decimal(0))
            ),
        )
    for f, digest in before.items():
        if sha256(reports / f) != digest:
            raise ValueError("既存対応・根拠・QAが変更された")
    if sha256(Path(prior["generation"]["output_path"])) != prior["generation"]["output_sha256"]:
        raise ValueError("既存generation longが変更された")
    summary = dict(
        analysis_use_counts={
            state: sum(r["analysis_use_diagnosis"] == state for r in diagnosis)
            for state in (NO_CONFLICT, CONDITIONAL, HOLD)
        },
        verification_status_counts=dict(Counter(r["verification_status"] for r in diagnosis)),
        source_competition_count=sum(r["source_candidate_count"] > 1 for r in diagnosis),
        target_reuse_count=sum(r["target_reused_by_source_count"] > 1 for r in diagnosis),
        gap=gap_summary,
        coverage=[r for r in comparison if r["dimension"] == "全国"],
        joins=joined_qa,
        observed_delivery_days=len(observed_days),
        delivery_date_min=min(observed_days),
        delivery_date_max=max(observed_days),
        null_cell_count=sum(r["null_cells"] for r in days),
        timezone="Asia/Tokyo",
        unit_conversion_performed=False,
        capacity=capacities,
        outage=outages,
        raw_sha_unchanged=True,
        original_input_sha256=before,
        original_long_sha_unchanged=True,
        output_directory=str(output),
        inquiry_work_in_scope=False,
        final_mapping_applied=False,
        baseline_selected=False,
        headroom_calculated=False,
    )
    (reports / "asset_analysis_readiness_qa_20261016.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary
