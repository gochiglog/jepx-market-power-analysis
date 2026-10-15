"""公式根拠を人手確認した候補の感度診断。正式mappingは変更しない。"""

import csv
import io
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from src.generation_hjks import read_raw
from src.raw_acquisition import detect_encoding
from src.time_normalization import sha256
from src.unmatched_generation import write_csv

# 公式PDF設備表（物理p2）と公開HJKS条件検索で確認。直接crosswalkではない。
REVIEW = {
    "GB583": ("富津", "futtsu", {str(n) for n in range(34028, 34049)}),
    "GB573": ("川崎", "kawasaki", {str(n) for n in range(34049, 34055)}),
    "GB593": ("千葉", "chiba", {str(n) for n in range(34001, 34012)}),
    "GB4Q3": ("姉崎", "anegasaki", {"328Z0", "328RV", "328RU"}),
    "GB553": ("常陸那珂", "hitachinaka", {"34091", "34092"}),
    "GAZT3": ("五井", "goi", {"330UQ", "331D8"}),
    "48193": ("西名古屋", "nishinagoya", {"42393"}),
    "GC5F3": ("五井", "goi", {"331RJ"}),
    "GV943": ("常陸那珂共同", "hitachinaka-kyodo", {"327YK"}),
    "GAWA3": ("横須賀", "yokosuka", {"328XX"}),
}


def coverage(assets, evidence, allowed):
    """元assetを一回だけ加算。plantは全構成unitに候補がある場合に数える。"""
    extra = defaultdict(set)
    for e in evidence:
        if e["verification_status"] in allowed:
            extra[e["asset_key_candidate"]].add(e["hjks_snapshot_row_id"])
    groups = defaultdict(list)
    for a in assets:
        groups[(a["area"], a["plant_code"], a["plant_name_normalized"])].append(a)
    unit_ok = {
        a["asset_key_candidate"]
        for a in assets
        if a["unit_match"] == "True" or len(extra[a["asset_key_candidate"]]) == 1
    }
    plant_ok = set()
    for group in groups.values():
        if all(a["plant_match"] == "True" for a in group) or all(
            len(extra[a["asset_key_candidate"]]) == 1 for a in group
        ):
            plant_ok.update(a["asset_key_candidate"] for a in group)
    total = sum((Decimal(a["generation_sum"]) for a in assets), Decimal(0))
    matched = sum(
        (Decimal(a["generation_sum"]) for a in assets if a["asset_key_candidate"] in plant_ok),
        Decimal(0),
    )
    matched_units_volume = sum(
        (Decimal(a["generation_sum"]) for a in assets if a["asset_key_candidate"] in unit_ok),
        Decimal(0),
    )
    result = dict(
        plant_count=len(groups),
        matched_plant_count=sum(
            all(a["asset_key_candidate"] in plant_ok for a in g) for g in groups.values()
        ),
        unit_count=len(assets),
        matched_unit_count=len(unit_ok),
        generation_coverage=str(matched / total),
        matched_generation_kwh=str(matched),
        unit_generation_coverage=str(matched_units_volume / total),
        unmatched_generation_kwh=str(total - matched),
        final_mapping_applied=False,
    )
    result["plant_match_rate"] = result["matched_plant_count"] / len(groups)
    result["unit_match_rate"] = len(unit_ok) / len(assets)
    result["area_coverage"] = {}
    for area in ("東京", "中部"):
        subset = [a for a in assets if a["area"] == area]
        denominator = sum((Decimal(a["generation_sum"]) for a in subset), Decimal(0))
        numerator = sum(
            (Decimal(a["generation_sum"]) for a in subset if a["asset_key_candidate"] in plant_ok),
            Decimal(0),
        )
        result["area_coverage"][area] = str(numerator / denominator) if denominator else None
    return result


def run(root: Path, repo: Path):
    reports = repo / "reports"
    prior = json.loads((reports / "generation_hjks_FY2025_qa.json").read_text())
    assets = list(csv.DictReader((reports / "generation_hjks_677_mapping_candidates.csv").open()))
    refs = json.loads(
        (repo / "metadata/research/asset_reference_manifest_20261016.json").read_text()
    )
    current = {}
    for ref in refs:
        assert sha256(root / ref["local_path"]) == ref["sha256"]
        if not ref["reference_id"].startswith("hjks_"):
            continue
        decoded, _ = detect_encoding((root / ref["local_path"]).read_bytes())
        rows = list(csv.DictReader(io.StringIO(decoded)))
        field = "発電所コード" if "plantcd" in ref["reference_id"] else "発電所名"
        value = ref["search_conditions"]["plantcd" if field == "発電所コード" else "name"]
        assert all(value in row[field] for row in rows), "公開検索条件が反映されていない"
        current[ref["reference_id"]] = (ref, rows)
    record = prior["snapshots"]["hjks_unit_677"]["source"]
    _, raw, _ = read_raw(root, record)
    old = {f"{record['sha256']}:{n}": row for n, row, _ in raw}
    identities = {
        (r["エリア"], r["発電所コード"], r["発電所名"], r["ユニット名"]) for r in old.values()
    }
    missing = []
    current_report = []
    for key, (ref, rows) in current.items():
        if "_name_" not in key:
            continue
        for row in rows:
            identity = tuple(row[k] for k in ("エリア", "発電所コード", "発電所名", "ユニット名"))
            report = {
                **{k: v for k, v in row.items() if k is not None},
                "reference_id": key,
                "source_url": ref["source_url"],
                "downloaded_at": ref["downloaded_at"],
                "present_in_677": identity in identities,
            }
            current_report.append(report)
            if identity not in identities:
                missing.append(report)
    write_csv(reports / "top10_current_hjks_units_20261016.csv", current_report)
    pairs = list(csv.DictReader((reports / "additional_mapping_candidates_20261016.csv").open()))
    evidence = []
    for pair in pairs:
        code = pair["generation_plant_code"]
        if code not in REVIEW or pair["C_strong_candidate"] != "True":
            continue
        name, slug, codes = REVIEW[code]
        if pair["hjks_plant_code"] not in codes:
            continue
        snapshot_row = old[pair["hjks_snapshot_row_id"]]
        ref, rows = current["hjks_name_" + name]
        matching = [
            r
            for r in rows
            if all(
                r[k] == snapshot_row[k]
                for k in ("エリア", "発電所コード", "発電所名", "ユニット名")
            )
        ]
        if len(matching) != 1:
            continue
        official = next(r for r in refs if r["reference_id"] == "jera_" + slug)
        pdf = next((r for r in refs if r["reference_id"] == "jera_" + slug + "_pamphlet_0"), None)
        evidence.append(
            {
                **pair,
                "verification_status": "strongly_supported",
                "equipment_basis": "公開HJKS設備名・号機と公式事業者の所在地・設備構成を照合",
                "hjks_reference_id": ref["reference_id"],
                "operator_reference_id": (pdf or official)["reference_id"],
                "source_url": (pdf or official)["source_url"],
                "source_page": "2（物理ページ）" if pdf else "基本情報",
                "direct_code_crosswalk_verified": False,
                "code_difference_reason": "未確認。改番・システム差と断定しない",
                "capacity_period_applied": False,
            }
        )
    write_csv(reports / "top10_official_mapping_evidence_20261016.csv", evidence)
    scenarios = {
        name: coverage(assets, evidence, allowed)
        for name, allowed in (
            ("A_strict", set()),
            ("B_confirmed", {"confirmed"}),
            ("C_sensitivity", {"confirmed", "strongly_supported"}),
        )
    }
    # 原CSVの燃種だけを取得。年データは再取得しない。
    fuels = defaultdict(set)
    for source in prior["generation"]["inputs"]:
        _, rows, _ = read_raw(root, {"local_path": source["path"], "sha256": source["sha256"]})
        for _, row, _ in rows:
            if row["発電所コード"] in REVIEW:
                key = (row["エリア"], row["発電所コード"], row["発電所名"], row["ユニット名"])
                fuels[key].add(row["発電方式・燃種"])
    structure = []
    for a in assets:
        if a["plant_code"] not in REVIEW:
            continue
        key = (a["area"], a["plant_code"], a["plant_name"], a["unit_name"])
        dates = json.loads(a["observed_dates"])
        structure.append(
            dict(
                area=a["area"],
                plant_code=a["plant_code"],
                plant_name=a["plant_name"],
                unit_name=a["unit_name"],
                fuel="|".join(sorted(fuels[key])),
                first_observed=min(dates),
                last_observed=max(dates),
                generation_kwh=a["generation_sum"],
            )
        )
    write_csv(reports / "top10_occto_equipment_structure_20261016.csv", structure)
    source_units = {e["asset_key_candidate"] for e in evidence}
    summary = dict(
        checked_on="2026-10-16",
        reference_files=len(refs),
        code_search_counts={k: len(v[1]) for k, v in current.items() if "_plantcd_" in k},
        current_rows=len(current_report),
        missing_from_677=missing,
        evidence_pair_counts={
            status: sum(e["verification_status"] == status for e in evidence)
            for status in (
                "confirmed",
                "strongly_supported",
                "candidate",
                "ambiguous",
                "unresolved",
            )
        },
        current_distinct_unit_count=len(
            {
                tuple(r[k] for k in ("エリア", "発電所コード", "発電所名", "ユニット名"))
                for r in current_report
            }
        ),
        supported_source_unit_count=len(source_units),
        supported_code_count=len({e["generation_plant_code"] for e in evidence}),
        confirmed=0,
        candidate=0,
        ambiguous=0,
        unresolved=0,
        code_crosswalk_unresolved_count=10,
        scenarios=scenarios,
        annual_redownload=False,
        existing_mapping_modified=False,
    )
    for path, expected in [(root / i["path"], i["sha256"]) for i in prior["generation"]["inputs"]]:
        assert sha256(path) == expected
    summary["existing_generation_raw_sha_unchanged"] = True
    (reports / "top10_official_review_qa_20261016.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary
