"""既存設備候補からHJKSの発電事業者記載を参照する。帰属や分類は決めない。"""

import json
import unicodedata
from collections import defaultdict
from decimal import Decimal

import pandas as pd

from src.generation_hjks import read_raw


def lexical_name(text):
    """字形・空白だけ。略称展開・法人の同一視をしない。"""
    return "".join(unicodedata.normalize("NFKC", text).split())


def inventory_operators(assets, mapping, raw_rows, snapshot, targets):
    if (
        assets.asset_key_candidate.duplicated().any()
        or mapping.asset_key_candidate.duplicated().any()
    ):
        raise ValueError("491unit対応表のキー重複")
    if set(assets.asset_key_candidate) != set(mapping.asset_key_candidate):
        raise ValueError("既存unitと診断対応表の母集団が異なる")
    master = {f"{snapshot['sha256']}:{n}": (n, r, extra) for n, r, extra in raw_rows}
    if len(master) != len(raw_rows):
        raise ValueError("HJKS行ID重複")
    by_asset = mapping.set_index("asset_key_candidate").to_dict("index")
    rows, edges = [], []
    target_reuse = defaultdict(set)
    for a in assets.to_dict("records"):
        m = by_asset[a["asset_key_candidate"]]
        direct = m["target_snapshot_row_id"]
        ids = [direct] if direct else [x for x in a["candidate_snapshot_rows"].split("|") if x]
        pool = [x for x in a["plant_candidate_snapshot_rows"].split("|") if x]
        route = "diagnostic_mapping" if direct else "strict_unit_candidates"
        if not ids:
            ids = pool
            route = "exploratory_plant_pool_only" if ids else "no_candidate"
        if len(ids) != len(set(ids)):
            raise ValueError("同一unit候補の重複参照")
        names = sorted({master[i][1]["発電事業者"] for i in ids})
        selected = master[direct][1]["発電事業者"] if direct else None
        status = (
            "scope_checked_unique_reference"
            if direct and m["research_use_status"] == "use_diagnostic"
            else "held_scope_or_period_unique_reference"
            if direct
            else "ambiguous_unit_candidates"
            if route == "strict_unit_candidates" and ids
            else route
        )
        entry = dict(a)
        entry.update({"diagnostic_mapping__" + c: v for c, v in m.items() if c not in a})
        entry.update(
            hjks_business_source_field="発電事業者",
            hjks_business_name_reference_status=status,
            hjks_business_name_unique_reference=selected,
            hjks_business_name_candidates_json=json.dumps(names, ensure_ascii=False),
            hjks_candidate_rows_json=json.dumps(ids),
            hjks_candidate_row_count=len(ids),
            hjks_business_name_candidate_count=len(names),
            candidate_equipment_route=route,
            snapshot_source_file=snapshot["local_path"],
            snapshot_source_sha256=snapshot["sha256"],
            snapshot_downloaded_at=snapshot.get("downloaded_at"),
            snapshot_registered_at=snapshot.get("registered_at"),
            snapshot_download_time_is_known=bool(snapshot.get("downloaded_at")),
            fy2025_asset_business_relationship_verified=False,
            owner_role_verified=False,
            operator_role_verified=False,
            seller_role_verified=False,
            contracted_supply_role_verified=False,
            role_limitation="発電事業者欄の記載のみ。所有・運営・売電・契約帰属と同一視しない",
            final_asset_attribution_assigned=False,
            generation_sum_kwh=Decimal(a["generation_sum"]),
        )
        rows.append(entry)
        for target in ids:
            number, raw, extra = master[target]
            edge = dict(
                asset_key_candidate=a["asset_key_candidate"],
                area=a["area"],
                hjks_snapshot_row_id=target,
                candidate_equipment_route=route,
                business_name=raw["発電事業者"],
                source_file=snapshot["local_path"],
                source_row=number,
                source_sha256=snapshot["sha256"],
                source_extra_cells_json=json.dumps(extra),
                equipment_reference_status=status,
                observed_generation_kwh_candidate_range=Decimal(a["generation_sum"]),
                is_exclusive_asset_generation_allocation=False,
                fy2025_relationship_verified=False,
            )
            edge.update({"hjks_raw__" + k: v for k, v in raw.items()})
            edges.append(edge)
            target_reuse[target].add(a["asset_key_candidate"])
    ledger = pd.DataFrame(rows)
    edge_frame = pd.DataFrame(edges)
    reuse = {k: len(v) for k, v in target_reuse.items()}
    ledger["candidate_target_reuse_max"] = [
        max((reuse[i] for i in json.loads(ids)), default=0)
        for ids in ledger.hjks_candidate_rows_json
    ]
    edge_frame["source_assets_referencing_this_target"] = [
        reuse[i] for i in edge_frame.hjks_snapshot_row_id
    ]
    # 法人名照合は別表。名称一致が資産帰属・dominant判定を表さない。
    comparisons = []
    for business in sorted(edge_frame.business_name.unique()):
        for target in targets.to_dict("records"):
            equal = business == target["regulatory_entity"]
            normalized = lexical_name(business) == lexical_name(target["regulatory_entity"])
            comparisons.append(
                dict(
                    hjks_business_name_raw=business,
                    regulatory_entity=target["regulatory_entity"],
                    regulatory_market_area=target["market_area"],
                    valid_from=target["valid_from"],
                    valid_to=target["valid_to"],
                    name_comparison="exact"
                    if equal
                    else "normalized_exact"
                    if normalized
                    else "different",
                    source_document=target["source_document"],
                    source_url=target["source_url"],
                    verification_level=target["verification_level"],
                    asset_attribution_established=False,
                    supplier_classification_assigned=False,
                )
            )
    # 排他的な主集計ではunitを一度だけ数える。
    exclusive = []
    for (area, status, business), g in ledger.assign(
        business_label=ledger.hjks_business_name_unique_reference.fillna("未確定（候補名は別表）")
    ).groupby(["area", "hjks_business_name_reference_status", "business_label"], sort=True):
        exclusive.append(
            dict(
                area=area,
                status=status,
                hjks_business_name=business,
                observed_unit_labels=len(g),
                observed_generation_kwh=sum(g.generation_sum_kwh),
                fy2025_relationship_verified_unit_count=0,
                role_assignment_verified_unit_count=0,
            )
        )
    # 候補範囲は同一unitが複数法人に含まれ得る。別表・非加算を明示。
    candidate_summary = []
    pairs = edge_frame.drop_duplicates(["asset_key_candidate", "business_name", "area"])
    for (area, business), g in pairs.groupby(["area", "business_name"], sort=True):
        candidate_summary.append(
            dict(
                area=area,
                hjks_business_name=business,
                candidate_unit_labels=len(g),
                observed_generation_kwh_candidate_range=sum(
                    g.observed_generation_kwh_candidate_range
                ),
                summable_across_businesses=False,
            )
        )
    qa = dict(
        rows=len(ledger),
        duplicate_keys=0,
        matched_mapping_rows=len(ledger),
        mapping_match_rate=1.0,
        unmatched_mapping_rows=0,
        row_growth=0,
        silent_drop=0,
        status_unit_counts=ledger.hjks_business_name_reference_status.value_counts().to_dict(),
        status_generation_kwh={
            k: str(sum(g.generation_sum_kwh))
            for k, g in ledger.groupby("hjks_business_name_reference_status")
        },
        candidate_edges=len(edge_frame),
        candidate_target_reuse_count=sum(n > 1 for n in reuse.values()),
        current_record_is_fy2025_attribution=False,
        final_attribution_or_classification=False,
        total_observed_generation_kwh=str(sum(ledger.generation_sum_kwh)),
    )
    return (
        ledger,
        edge_frame,
        pd.DataFrame(exclusive),
        pd.DataFrame(candidate_summary),
        pd.DataFrame(comparisons),
        qa,
    )


def load_master(root, prior):
    snapshot = prior["snapshots"]["hjks_unit_677"]["source"]
    _, rows, _ = read_raw(root, snapshot)
    return rows, snapshot


def area_reference_summary(ledger):
    """設備候補の確認水準別に一度だけ数える。法人別の候補範囲とは別。"""
    rows = []
    for area, group in ledger.groupby("area"):
        checked = group.hjks_business_name_reference_status.eq("scope_checked_unique_reference")
        held = group.hjks_business_name_reference_status.eq("held_scope_or_period_unique_reference")
        total = sum(group.generation_sum_kwh)
        rows.append(
            dict(
                area=area,
                observed_unit_labels=len(group),
                observed_generation_kwh=total,
                scope_checked_reference_unit_labels=int(checked.sum()),
                scope_checked_reference_generation_kwh=sum(
                    group.loc[checked, "generation_sum_kwh"]
                ),
                held_unique_reference_unit_labels=int(held.sum()),
                held_unique_reference_generation_kwh=sum(group.loc[held, "generation_sum_kwh"]),
                unresolved_unit_labels=int((~checked & ~held).sum()),
                unresolved_observed_generation_kwh=sum(
                    group.loc[~checked & ~held, "generation_sum_kwh"]
                ),
                scope_checked_observed_generation_share=(
                    sum(group.loc[checked, "generation_sum_kwh"]) / total if total else None
                ),
            )
        )
    return pd.DataFrame(rows)


def business_context_summary(ledger, contexts):
    """根拠資料の対象法人名を比較用に表示する。法人帰属の新規対応付けはしない。"""
    rows = []
    for context in contexts:
        for name in context.get("business_names_to_compare", []):
            group = ledger[ledger.hjks_business_name_unique_reference.eq(name)]
            rows.append(
                dict(
                    hjks_business_name_comparison_label=name,
                    document_id=context["document_id"],
                    source_url=context["url"],
                    event_date=context["event_date"],
                    unique_reference_unit_labels=len(group),
                    observed_generation_kwh_reference_range=sum(group.generation_sum_kwh),
                    relationship_scope=context.get(
                        "relationship_scope", "general_business_context_only"
                    ),
                    fy2025_corporate_contract_existence_documented=context.get(
                        "fy2025_corporate_contract_existence_documented", False
                    ),
                    fy2025_unit_attribution_verified=False,
                    notes=context["applicability"],
                )
            )
    return pd.DataFrame(rows)
