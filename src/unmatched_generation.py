"""未対応発電量の分解と追加対応の条件付き上限。既存mappingは変更しない。"""

import csv
import json
import re
from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

from src.generation_hjks import asset_id, attributes, normalize_name, read_raw
from src.time_normalization import sha256


def lexical(value):
    """診断用の字形キー。元列・既存正規化列へは適用しない。"""
    return re.sub(r"\s+", "", normalize_name(value))


def roman_key(value):
    romans = {
        "I": "1",
        "II": "2",
        "III": "3",
        "IV": "4",
        "V": "5",
        "VI": "6",
        "VII": "7",
        "VIII": "8",
        "IX": "9",
        "X": "10",
    }
    return re.sub(
        r"(?<![A-Za-z])(?:VIII|VII|III|II|VI|IV|IX|V|X|I)(?![A-Za-z])",
        lambda m: romans[m[0]],
        lexical(value),
    )


def plant_stem(value):
    """設備語尾・号機等の診断用基部。意味的同一性の証明ではない。"""
    s = roman_key(value).translate(str.maketrans("", "", "()（）[]［］【】"))
    s = re.sub(r"新?\d+号(?:系列\d+軸|機)?$", "", s)
    return re.sub(r"(?:火力発電所|火力発電機|発電所)$", "", s)


def unit_token(value):
    s = roman_key(value).translate(str.maketrans("", "", "()（）[]［］【】"))
    # 数字間の長音記号だけを診断上の区切りとみなす。名称の長音は変えない。
    s = re.sub(r"(?<=\d)ー(?=\d)", "-", s)
    match = re.search(r"(新?\d+(?:-\d+)*(?:GT[A-Z]+)?)(?:号機|号|軸)?$", s)
    return match[1] if match else None


def common_prefix(a, b):
    result = ""
    for left, right in zip(a, b):
        if left != right:
            break
        result += left
    return result


def code_features(a, b):
    prefix = common_prefix(a, b)
    suffix = common_prefix(a[::-1], b[::-1])[::-1]
    return {
        "code_equal": bool(a) and a == b,
        "common_prefix": prefix,
        "common_suffix": suffix,
        "generation_suffix_after_prefix": a[len(prefix) :],
        "hjks_suffix_after_prefix": b[len(prefix) :],
        "code_relation": "exact"
        if a and a == b
        else "prefix_3plus"
        if len(prefix) >= 3
        else "suffix_3plus"
        if len(suffix) >= 3
        else "different",
    }


def score_pair(g, h):
    features = code_features(g["plant_code"], h["plant_code"])
    gp, hp = lexical(g["plant_name_raw"]), lexical(h["plant_name_raw"])
    gu, hu = lexical(g["unit_name_raw"]), lexical(h["unit_name_raw"])
    p = SequenceMatcher(None, gp, hp).ratio() if gp and hp else 0.0
    u = SequenceMatcher(None, gu, hu).ratio() if gu and hu else 0.0
    area = bool(g["area"]) and g["area"] == h["area"]
    plant_exact = bool(gp) and gp == hp
    unit_exact = bool(gu) and gu == hu
    stem = bool(plant_stem(g["plant_name_raw"])) and plant_stem(g["plant_name_raw"]) == plant_stem(
        h["plant_name_raw"]
    )
    token = unit_token(g["unit_name_raw"])
    same_token = token is not None and token == unit_token(h["unit_name_raw"])
    return {
        **features,
        "area_equal": area,
        "plant_name_similarity": p,
        "unit_name_similarity": u,
        "similarity": 0.7 * p + 0.3 * u,
        "plant_lexical_exact": plant_exact,
        "unit_lexical_exact": unit_exact,
        "diagnostic_stem_equal": stem,
        "diagnostic_unit_token_equal": same_token,
        "B_exact_candidate": area and plant_exact and unit_exact,
        "C_strong_candidate": area and stem and same_token,
        "diagnostic_rule_tags": (
            "設備語尾・括弧記号・号機/軸・数字間長音・Roman番号を候補内でのみ比較"
        ),
        "code_mismatch_requires_review": not features["code_equal"],
    }


def generate_pairs(generation, master):
    pairs = []
    for aid, g in generation.items():
        if g["plant_match"] and g["unit_match"]:
            continue
        scores = []
        for h in master:
            f = score_pair(g, h)
            if f["area_equal"] or f["code_equal"]:
                scores.append(
                    {
                        "asset_key_candidate": aid,
                        **{
                            f"generation_{k}": g[k]
                            for k in ("area", "plant_code", "plant_name_raw", "unit_name_raw")
                        },
                        **{
                            f"hjks_{k}": h[k]
                            for k in ("area", "plant_code", "plant_name_raw", "unit_name_raw")
                        },
                        "hjks_snapshot_row_id": h["snapshot_row_id"],
                        **f,
                        "final_mapping": False,
                    }
                )
        top = {
            r["hjks_snapshot_row_id"]
            for r in sorted(
                (s for s in scores if s["area_equal"]), key=lambda r: r["similarity"], reverse=True
            )[:5]
        }
        for row in scores:
            if (
                row["hjks_snapshot_row_id"] in top
                or row["code_relation"] != "different"
                or row["plant_lexical_exact"]
                or row["unit_lexical_exact"]
                or row["C_strong_candidate"]
            ):
                row["fuzzy_top5_candidate"] = row["hjks_snapshot_row_id"] in top
                row["review_status"] = "manual_review"
                pairs.append(row)
    return pairs


def plant_key(row):
    return (row["area"], row["plant_code"], row["plant_name_normalized"])


def code_census(generation, master):
    g = defaultdict(list)
    h = defaultdict(list)
    for r in generation.values():
        g[(r["area"], r["plant_code"])].append(r)
    for r in master:
        h[(r["area"], r["plant_code"])].append(r)
    rows = []
    for (ga, gc), gs in g.items():
        for (ha, hc), hs in h.items():
            f = code_features(gc, hc)
            if not (f["code_equal"] or ga == ha and f["code_relation"] != "different"):
                continue
            rows.append(
                {
                    "generation_area": ga,
                    "generation_code": gc,
                    "hjks_area": ha,
                    "hjks_code": hc,
                    **f,
                    "area_equal": ga == ha,
                    "generation_plant_label_count": len({r["plant_name_normalized"] for r in gs}),
                    "generation_observed_unit_count": len(gs),
                    "hjks_plant_name_variant_count": len({r["plant_name_normalized"] for r in hs}),
                    "hjks_unit_rows": len(hs),
                    "official_crosswalk_verified": False,
                }
            )
    return rows, {
        "generation_area_code_count": len(g),
        "hjks_area_code_count": len(h),
        "exact_area_code_count": len(g.keys() & h.keys()),
        "same_code_different_area_pairs": sum(
            r["code_equal"] and not r["area_equal"] for r in rows
        ),
        "prefix_or_suffix_only_pair_count": sum(not r["code_equal"] for r in rows),
        "scope": "全491発電unit候補・677台帳行。prefix/suffixは文字列関係のみ",
    }


def scenario_stats(generation, pairs):
    groups = defaultdict(list)
    for aid, row in generation.items():
        groups[plant_key(row)].append(aid)
    by_asset = defaultdict(list)
    for row in pairs:
        by_asset[row["asset_key_candidate"]].append(row)
    total = sum((r["generation_sum"] for r in generation.values()), Decimal(0))
    result = []
    accepted_by_scenario = {}
    for scenario in ("A", "B", "C"):
        accepted = set()
        plant_count = ambiguous = added = 0
        proposed_edges = []
        for key, aids in groups.items():
            if all(generation[a]["plant_match"] for a in aids):
                accepted.update(aids)
                plant_count += 1
                continue
            target_sets = []
            for aid in aids:
                if scenario == "A":
                    targets = set()
                else:
                    targets = {
                        (r["hjks_area"], r["hjks_plant_code"])
                        for r in by_asset[aid]
                        if r["B_exact_candidate"] or scenario == "C" and r["C_strong_candidate"]
                    }
                target_sets.append(targets)
            union = set().union(*target_sets)
            unique = bool(target_sets) and all(target_sets) and len(union) == 1
            if unique:
                accepted.update(aids)
                plant_count += 1
                added += 1
                proposed_edges.append((key, next(iter(union))))
            else:
                ambiguous += len(union) > 1 or any(generation[a]["plant_ambiguous"] for a in aids)
        volume = sum((generation[a]["generation_sum"] for a in accepted), Decimal(0))
        reuse = defaultdict(set)
        for source, target in proposed_edges:
            reuse[target].add(source)
        result.append(
            {
                "scenario": scenario,
                "source_plant_label_count": len(groups),
                "matched_plant_label_count": plant_count,
                "plant_match_rate": plant_count / len(groups),
                "ambiguous_plant_label_count": ambiguous,
                "added_plant_label_count": added,
                "matched_generation_kwh": str(volume),
                "total_observed_generation_kwh": str(total),
                "generation_coverage": str(volume / total),
                "unmatched_generation_kwh": str(total - volume),
                "additional_target_reuse_count": sum(len(v) > 1 for v in reuse.values()),
                "hypothesis_only": True,
                "final_mapping_applied": False,
            }
        )
        accepted_by_scenario[scenario] = accepted
    return result, accepted_by_scenario


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run(root: Path, repo: Path):
    reports = repo / "reports"
    previous = json.loads((reports / "generation_hjks_FY2025_qa.json").read_text())
    generation = {}
    for row in csv.DictReader((reports / "generation_hjks_677_mapping_candidates.csv").open()):
        for field in ("generation_sum", "absolute_generation_sum"):
            row[field] = Decimal(row[field])
        for field in ("raw_day_rows", "long_rows", "plant_candidate_count", "unit_candidate_count"):
            row[field] = int(row[field])
        for field in ("plant_match", "unit_match", "plant_ambiguous", "unit_ambiguous"):
            row[field] = row[field] == "True"
        generation[row["asset_key_candidate"]] = row
    record = previous["snapshots"]["hjks_unit_677"]["source"]
    _, raw, _ = read_raw(root, record)
    master = [
        {**attributes(row), "snapshot_row_id": f"{record['sha256']}:{n}"} for n, row, _ in raw
    ]
    pairs = generate_pairs(generation, master)
    census, census_summary = code_census(generation, master)
    write_csv(reports / "code_structure_census_20261016.csv", census)
    scenarios, accepted = scenario_stats(generation, pairs)
    code_supported_pairs = [
        dict(r, C_strong_candidate=r["C_strong_candidate"] and r["code_equal"]) for r in pairs
    ]
    code_supported, _ = scenario_stats(generation, code_supported_pairs)
    total = Decimal(scenarios[0]["total_observed_generation_kwh"])
    expected = Decimal(previous["mapping"]["hjks_unit_677"]["plant"]["generation_kwh_matched"])
    if Decimal(scenarios[0]["matched_generation_kwh"]) != expected:
        raise ValueError("既存厳格coverageと不一致")
    unplant = {a for a, r in generation.items() if not r["plant_match"]}
    ununit = {a for a, r in generation.items() if not r["unit_match"]}
    # 月と燃種はrawから再検査。空セルを0という観測値には変えず、数値観測分のみ加算。
    aggregates = {
        name: defaultdict(
            lambda: {"total": Decimal(0), "unmatched": Decimal(0), "nulls": 0, "unmatched_nulls": 0}
        )
        for name in ("area", "month", "code", "fuel", "diagnostic_cluster")
    }
    source_total = Decimal(0)
    source_asset_totals = defaultdict(Decimal)
    source_asset_nulls = defaultdict(int)
    source_nulls = 0
    observed_assets = set()
    for source in previous["generation"]["inputs"]:
        record_for_read = {"local_path": source["path"], "sha256": source["sha256"]}
        headers, rows, _ = read_raw(root, record_for_read)
        columns = [c for c in headers if re.fullmatch(r"\d\d:\d\d\[kWh\]", c)]
        if len(columns) != 48:
            raise ValueError("raw48列不一致")
        for _, row, _ in rows:
            aid = asset_id(row)
            if aid not in generation:
                raise ValueError("既存候補にない発電キー")
            observed_assets.add(aid)
            values = [Decimal(row[c]) for c in columns if row[c] != ""]
            if any(not v.is_finite() for v in values):
                raise ValueError("非有限値")
            amount = sum(values, Decimal(0))
            nulls = 48 - len(values)
            source_total += amount
            source_asset_totals[aid] += amount
            source_asset_nulls[aid] += nulls
            source_nulls += nulls
            keys = {
                "area": row["エリア"],
                "month": row["対象日"][:7].replace("/", "-"),
                "code": (row["エリア"], row["発電所コード"]),
                "fuel": row["発電方式・燃種"],
                "diagnostic_cluster": (row["エリア"], plant_stem(row["発電所名"])),
            }
            for name, key in keys.items():
                stats = aggregates[name][key]
                stats["total"] += amount
                stats["nulls"] += nulls
                if aid in unplant:
                    stats["unmatched"] += amount
                    stats["unmatched_nulls"] += nulls
        print(f"内訳検査: {source['path']}", flush=True)
    if source_total != total or observed_assets != set(generation):
        raise ValueError("原本との母集団/総量不一致")
    if any(source_asset_totals[a] != generation[a]["generation_sum"] for a in generation):
        raise ValueError("資産ごとの原本発電量と既存候補統計が不一致")
    unmatched_total = total - expected
    for name, groups in aggregates.items():
        data = []
        for key, stats in groups.items():
            dimensions = (
                {"area": key[0], "plant_code": key[1]}
                if name == "code"
                else {"area": key[0], "diagnostic_plant_stem": key[1]}
                if name == "diagnostic_cluster"
                else {"fuel_raw" if name == "fuel" else name: key}
            )
            data.append(
                {
                    **dimensions,
                    "total_observed_generation_kwh": stats["total"],
                    "unmatched_generation_kwh": stats["unmatched"],
                    "null_cell_count": stats["nulls"],
                    "unmatched_null_cell_count": stats["unmatched_nulls"],
                    "share_of_unmatched": stats["unmatched"] / unmatched_total,
                    "unmatched_share_within_group": stats["unmatched"] / stats["total"]
                    if stats["total"]
                    else "",
                }
            )
        data.sort(key=lambda r: r["unmatched_generation_kwh"], reverse=True)
        if sum(r["unmatched_generation_kwh"] for r in data) != unmatched_total:
            raise ValueError("内訳加算不一致")
        write_csv(reports / f"unmatched_generation_by_{name}_20261016.csv", data)
    pair_index = defaultdict(list)
    for row in pairs:
        pair_index[row["asset_key_candidate"]].append(row)
    grouped = defaultdict(list)
    for aid in unplant:
        grouped[plant_key(generation[aid])].append(aid)
    plants = []
    for (area, code, name), aids in grouped.items():
        volume = sum((generation[a]["generation_sum"] for a in aids), Decimal(0))
        exact_count = max(generation[a]["plant_candidate_count"] for a in aids)
        strong = {
            (r["hjks_area"], r["hjks_plant_code"])
            for a in aids
            for r in pair_index[a]
            if r["C_strong_candidate"]
        }
        why = (
            "同area・同plant名で複数HJKSコード"
            if exact_count > 1
            else "code×areaおよび字形plant名で一意候補なし"
        )
        if any(a in accepted["C"] for a in aids):
            why += "。設備語尾/号機/基部・unit記号による条件付き候補あり"
        plants.append(
            {
                "area": area,
                "plant_code": code,
                "plant_name_raw": "|".join(sorted({generation[a]["plant_name_raw"] for a in aids})),
                "plant_name_normalized": name,
                "observed_unit_count": len(aids),
                "null_cell_count": sum(source_asset_nulls[a] for a in aids),
                "generation_kwh_FY2025": volume,
                "share_of_all_observed_generation": volume / total,
                "share_of_unmatched": volume / unmatched_total,
                "hjks_strict_candidate_count": exact_count,
                "strong_candidate_code_count": len(strong),
                "status": "ambiguous" if exact_count > 1 else "unmatched",
                "reason_candidate": why,
                "B_hypothetical_match": all(a in accepted["B"] for a in aids),
                "C_hypothetical_match": all(a in accepted["C"] for a in aids),
                "final_mapping": False,
            }
        )
    plants.sort(
        key=lambda r: (
            -r["generation_kwh_FY2025"],
            r["area"],
            r["plant_code"],
            r["plant_name_normalized"],
        )
    )
    write_csv(reports / "unmatched_plants_20261016.csv", plants)
    units = []
    for aid in ununit:
        r = generation[aid]
        units.append(
            {
                k: r[k]
                for k in (
                    "area",
                    "plant_code",
                    "plant_name_raw",
                    "unit_name_raw",
                    "generation_sum",
                    "plant_match",
                    "unit_candidate_count",
                )
            }
            | {
                "asset_key_candidate": aid,
                "category": "plant_matched_unit_unmatched"
                if r["plant_match"]
                else "plant_and_unit_unmatched",
                "share_of_all_observed_generation": r["generation_sum"] / total,
                "final_mapping": False,
            }
        )
    units.sort(key=lambda r: (-r["generation_sum"], r["asset_key_candidate"]))
    write_csv(reports / "unmatched_units_20261016.csv", units)
    write_csv(reports / "additional_mapping_candidates_20261016.csv", pairs)
    write_csv(reports / "coverage_scenarios_20261016.csv", scenarios)
    # コード対応は候補グラフ。数値のprefix/suffixをコード体系の公式仕様とみなさない。
    edges = {}
    for r in pairs:
        if not (
            r["B_exact_candidate"] or r["C_strong_candidate"] or r["code_relation"] != "different"
        ):
            continue
        key = (
            r["generation_area"],
            r["generation_plant_code"],
            r["hjks_area"],
            r["hjks_plant_code"],
        )
        e = edges.setdefault(
            key,
            {
                k: r[k]
                for k in (
                    "generation_area",
                    "generation_plant_code",
                    "hjks_area",
                    "hjks_plant_code",
                    "code_relation",
                    "common_prefix",
                    "common_suffix",
                    "generation_suffix_after_prefix",
                    "hjks_suffix_after_prefix",
                )
            },
        )
        e["strong_name_unit_evidence"] = (
            e.get("strong_name_unit_evidence", False) or r["C_strong_candidate"]
        )
    source_edges = defaultdict(set)
    target_edges = defaultdict(set)
    for key, e in edges.items():
        if e["strong_name_unit_evidence"]:
            source_edges[key[:2]].add(key[2:])
            target_edges[key[2:]].add(key[:2])
    for key, e in edges.items():
        e.update(
            strong_targets_per_source_code=len(source_edges[key[:2]]),
            strong_sources_per_target_code=len(target_edges[key[2:]]),
            one_to_many=len(source_edges[key[:2]]) > 1,
            many_to_one=len(target_edges[key[2:]]) > 1,
            official_crosswalk_verified=False,
        )
    write_csv(reports / "plant_code_relations_20261016.csv", list(edges.values()))
    categories = {}
    for category in {r["category"] for r in units}:
        members = [r for r in units if r["category"] == category]
        categories[category] = {
            "unit_count": len(members),
            "generation_kwh": str(sum((r["generation_sum"] for r in members), Decimal(0))),
        }
    summary = {
        "checked_on": "2026-10-16",
        "reference_unit_rows": 677,
        "reference_use_only": True,
        "total_observed_generation_kwh": str(total),
        "unmatched_generation_kwh": str(unmatched_total),
        "unmatched_fraction": str(unmatched_total / total),
        "unmatched_plant_label_count": len(plants),
        "top10_unmatched_share": str(
            sum((r["generation_kwh_FY2025"] for r in plants[:10]), Decimal(0)) / unmatched_total
        ),
        "unit_unmatched_categories": categories,
        "scenarios": scenarios,
        "C_requiring_exact_code_also": code_supported[-1],
        "pair_count": len(pairs),
        "code_census": census_summary,
        "unmatched_positive_plant_label_count": sum(r["generation_kwh_FY2025"] > 0 for r in plants),
        "positive_unmatched_code_count": sum(
            s["unmatched"] > 0 for s in aggregates["code"].values()
        ),
        "positive_unmatched_diagnostic_cluster_count": sum(
            s["unmatched"] > 0 for s in aggregates["diagnostic_cluster"].values()
        ),
        "top10_code_share_of_unmatched": str(
            sum(
                sorted((s["unmatched"] for s in aggregates["code"].values()), reverse=True)[:10],
                Decimal(0),
            )
            / unmatched_total
        ),
        "top10_diagnostic_cluster_share_of_unmatched": str(
            sum(
                sorted(
                    (s["unmatched"] for s in aggregates["diagnostic_cluster"].values()),
                    reverse=True,
                )[:10],
                Decimal(0),
            )
            / unmatched_total
        ),
        "null_cell_count": source_nulls,
        "raw_amounts_reconciled": True,
        "C_caution": (
            "コード不一致を含む名称・unit記号の条件付き上限。"
            "公式コード対応・時点・物理集約は未確認。採用しない"
        ),
        "original_input_sha256": {
            str(p.relative_to(repo)): sha256(p)
            for p in (
                reports / "generation_hjks_677_mapping_candidates.csv",
                reports / "generation_hjks_FY2025_qa.json",
            )
        },
        "generation_interim_unchanged": sha256(Path(previous["generation"]["output_path"]))
        == previous["generation"]["output_sha256"],
        "raw_unchanged": all(
            sha256(root / r["path"]) == r["sha256"] for r in previous["generation"]["inputs"]
        )
        and sha256(root / record["local_path"]) == record["sha256"],
        "final_mapping_applied": False,
        "headroom_calculated": False,
    }
    (reports / "unmatched_generation_summary_20261016.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary
