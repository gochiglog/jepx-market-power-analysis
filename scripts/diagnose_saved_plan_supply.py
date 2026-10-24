"""翌々日保存計画と参照上限の診断。実データの閉じた2地域RSIは作らない。"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.inspect_rsi_inputs import digest  # noqa: E402
from src.saved_plan_supply import (  # noqa: E402
    AREAS,
    CODE,
    HORIZON,
    PROFILES,
    STATUS,
    STRATEGIES,
    adjust_upper,
    context_policies,
    decimal_to_micro,
    micro_to_decimal,
)

N = len(AREAS) * HORIZON
EPOCH_DAY = (date(2025, 4, 1) - date(1970, 1, 1)).days
COUNTS = [
    "observed_unit_count",
    "reference_known_count",
    "capacity_held_count",
    "scenario_defined_count",
    "record_held_count",
    "negative_upper_count",
    "stop_zero_count",
    "mean_subtraction_count",
    "record_absent_no_deduction_count",
    "outside_endpoint_count",
    "copy_reference_count",
    "boundary_assumption_count",
    "partial_start_held_count",
    "generation_missing_count",
]


def slots(table):
    days = pc.cast(table["delivery_date"], pa.int32()).to_numpy()
    return (days - EPOCH_DAY) * 48 + table["period"].to_numpy() - 1


def ids(table):
    area = pc.fill_null(pc.index_in(table["area"], value_set=pa.array(AREAS)), -1).to_numpy()
    time = slots(table)
    if np.any(area < 0) or np.any(time < 0) or np.any(time >= HORIZON):
        raise ValueError("地域/年度キー不一致")
    return area * HORIZON + time, area, time


def checked_writer(table, writer, path):
    if writer is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = pq.ParquetWriter(path, table.schema, compression="zstd")
    writer.write_table(table)
    return writer


def network_diagnostic(root, path, output):
    columns = [
        "source_record_id",
        "delivery_date",
        "period",
        "link_name_raw",
        "direction_raw",
        "sending_area",
        "receiving_area",
        "equipment_scope_class",
        "scope_phase",
        "direction_status",
        "operating_capacity_mw",
        "margin_mw",
        "wide_area_adjustment_mw",
        "planned_flow_mw",
        "available_capacity_mw",
        "source_acquired_at",
        "row_updated_at",
        "formal_full_FY_validity_verified",
    ]
    table = pq.read_table(path)
    columns = table.column_names
    frame = table.to_pandas()
    if frame.duplicated(["delivery_date", "period", "link_name_raw", "direction_raw"]).any():
        raise ValueError("系統行の重複")
    sign = np.where(frame.direction_raw.eq("順方向"), 1, -1)
    # あくまで包絡の診断候補。未定義のフェンスを補ったことにはしない。
    values = frame.operating_capacity_mw - frame.margin_mw - frame.wide_area_adjustment_mw
    frame["candidate_direction_envelope_mw"] = values * sign
    frame["candidate_envelope_negative"] = frame.candidate_direction_envelope_mw.lt(0)
    frame["network_constraint_definition_complete"] = False
    frame["all_suppliers_lp_status"] = "held_fence_coefficients_and_scope"
    frame["market_bid_information_set_reproduced"] = False
    derived = table
    for column in [
        "candidate_direction_envelope_mw",
        "candidate_envelope_negative",
        "network_constraint_definition_complete",
        "all_suppliers_lp_status",
        "market_bid_information_set_reproduced",
    ]:
        values = (
            pa.array(frame[column], type=pa.decimal128(28, 6))
            if column.endswith("_mw")
            else pa.array(frame[column])
        )
        derived = derived.append_column(column, values)
    pq.write_table(derived, output / "network_candidate_bounds.parquet", compression="zstd")
    inventory = (
        frame.groupby(["link_name_raw", "direction_raw"], observed=True)
        .agg(
            rows=("period", "size"),
            first_date=("delivery_date", "min"),
            last_date=("delivery_date", "max"),
            negative_bounds=("candidate_envelope_negative", "sum"),
            scope=("equipment_scope_class", "first"),
        )
        .reset_index()
    )
    inventory.to_csv(root / "reports/16_saved_plan_supply/qa/network_inventory.csv", index=False)
    return {
        "source_rows": len(frame),
        "preserved_source_columns": columns,
        "negative_candidate_bounds": int(frame.candidate_envelope_negative.sum()),
        "candidate_single_area_rows": int(
            (frame.sending_area.notna() & frame.receiving_area.notna()).sum()
        ),
        "all_firm_lp_ready_times": 0,
        "held_times": HORIZON,
        "blockers": [
            "北陸フェンス等の期間別係数・独立辺との包含範囲が未確定",
            "中部/関西フェンス追加後の同時制約入力が未確定",
            "個別系統の全FY物理包含・包絡の有効期間は未確認",
        ],
        "unknown_fences_ignored": False,
        "real_two_area_model_run": False,
    }


def run(root, output):
    if output.exists():
        raise ValueError("出力先が存在する。固定版を上書きしない")
    artifact = json.loads((root / "metadata/artifact_catalog.json").read_text())["artifacts"]

    def select(token):
        matches = [root / r["path"] for r in artifact if token in r["path"]]
        if len(matches) != 1:
            raise ValueError("入力選択不明: " + token)
        return matches[0]

    source = select("generation_with_snapshot_public_records_FY2025")
    base_dir = source.parent
    context_path = base_dir / "public_record_contexts.parquet"
    contents_path = base_dir / "record_contents.parquet"
    plan_path = select("occto_reserve_nextday2_FY2025")
    network_path = select("system_capacity_dayahead2_planned_direction_comparison_FY2025")
    assets_path = select("observed_unit_business_evidence_FY2025")
    paths = [
        source,
        context_path,
        contents_path,
        plan_path,
        network_path,
        assets_path,
        root / "metadata/research/regulatory_targets.csv",
        root / "metadata/research/legal_name_alias_evidence_20261016.csv",
    ]
    inputs = [{"path": str(p.relative_to(root)), "sha256": digest(p)} for p in paths]
    contexts = pq.read_table(context_path).to_pylist()
    contents = {r["content_id"]: r for r in pq.read_table(contents_path).to_pylist()}
    policies = {strategy: context_policies(contexts, contents, strategy) for strategy in STRATEGIES}
    targets = pd.read_csv(paths[-2], dtype=str)
    firms = targets.regulatory_entity.drop_duplicates().tolist()
    aliases = pd.read_csv(paths[-1], dtype=str)
    alias = dict(zip(aliases.alias_raw, aliases.canonical_legal_name, strict=True))
    assets = pq.read_table(assets_path).to_pandas()
    names = assets.hjks_business_name_unique_reference.map(lambda x: alias.get(x, x))
    eligible = assets.hjks_business_name_reference_status.eq("scope_checked_unique_reference")
    entity_ids = np.array(
        [firms.index(x) if ok and x in firms else -1 for x, ok in zip(names, eligible, strict=True)]
    )
    named_global = {
        i: bool(((entity_ids == i) & assets.area.isin(AREAS[:9])).any()) for i in range(len(firms))
    }
    union_counts = assets.groupby("area").size().reindex(AREAS).to_numpy()
    plan = pq.read_table(plan_path)
    gid, _, _ = ids(plan)
    if len(plan) != N or len(np.unique(gid)) != N:
        raise ValueError("翌々日需要の10地域×365×48キー不一致")
    plan = pc.take(plan, pa.array(np.argsort(gid)))
    demand = np.array([float(v) for v in plan["area_demand_forecast_mw"].to_pylist()])
    if not np.isfinite(demand).all() or (demand <= 0).any():
        raise ValueError("需要の欠測/非正値")
    output.mkdir(parents=True)
    network_qa = network_diagnostic(root, network_path, output)
    profiles = [("reference_no_deduction", None, None)] + [
        (profile + "__" + strategy, profile, strategy)
        for profile in PROFILES
        for strategy in STRATEGIES
    ]
    summaries, all_status, row_checks, original_sums = [], [], {}, None
    region_writer = nation_writer = firm_writer = None
    for label, profile, strategy in profiles:
        counts = {k: np.zeros(N, dtype=np.int32) for k in COUNTS}
        total = np.zeros(N, dtype=np.int64)
        reference = np.zeros(N, dtype=np.int64)
        firm_sum = np.zeros((len(firms), N), dtype=np.int64)
        firm_held = np.zeros((len(firms), N), dtype=np.int32)
        firm_negative = np.zeros((len(firms), N), dtype=np.int32)
        status_counts = np.zeros(len(STATUS), dtype=np.int64)
        writer, offset = None, 0
        columns = [
            "asset_key_candidate",
            "area",
            "delivery_date",
            "period",
            "capacity_reference_kw",
            "qa_generation_missing",
        ]
        if profile:
            columns += [
                "pubrec_" + profile + "_context_id",
                "pubrec_" + profile + "_state",
                "pubrec_" + profile + "_has_full_scenario_record",
            ]
        path = output / "unit_scenarios" / (label + ".parquet")
        for batch in pq.ParquetFile(source).iter_batches(columns=columns, batch_size=65536):
            table = pa.Table.from_batches([batch])
            index, _, time = ids(table)
            aid = pc.fill_null(
                pc.index_in(
                    table["asset_key_candidate"], value_set=pa.array(assets.asset_key_candidate)
                ),
                -1,
            ).to_numpy()
            if np.any(aid < 0):
                raise ValueError("未一致設備行をdropしない")
            cap, cap_valid = decimal_to_micro(table["capacity_reference_kw"])
            if profile:
                ctx = table["pubrec_" + profile + "_context_id"].to_numpy()
                state = table["pubrec_" + profile + "_state"].to_numpy()
                adjusted, valid, status, boundary, copies = adjust_upper(
                    cap, cap_valid, ctx, time, policies[strategy], source_states=state
                )
            else:
                adjusted, valid = cap.copy(), cap_valid.copy()
                status = np.where(
                    cap_valid, CODE["reference_without_deduction"], CODE["held_reference_capacity"]
                ).astype(np.uint8)
                boundary, copies = np.zeros(len(cap), bool), np.zeros(len(cap), np.int32)
            negative = valid & (adjusted < 0)
            masks = [
                np.ones(len(cap), bool),
                cap_valid,
                ~cap_valid,
                valid,
                cap_valid & ~valid,
                negative,
                status == 1,
                status == 2,
                status == 0,
                status == 3,
                copies > 0,
                boundary & valid,
                status == 11,
                table["qa_generation_missing"].to_numpy(),
            ]
            for key, mask in zip(COUNTS, masks, strict=True):
                np.add.at(counts[key], index[mask], 1)
            np.add.at(reference, index[cap_valid], cap[cap_valid])
            np.add.at(total, index[valid], adjusted[valid])
            entity = entity_ids[aid]
            identified = entity >= 0
            good = identified & valid
            np.add.at(firm_sum, (entity[good], index[good]), adjusted[good])
            bad = identified & ~valid
            np.add.at(firm_held, (entity[bad], index[bad]), 1)
            bad = identified & negative
            np.add.at(firm_negative, (entity[bad], index[bad]), 1)
            status_counts += np.bincount(status, minlength=len(STATUS))
            attachment = table.append_column(
                "source_observation_row", pa.array(np.arange(offset, offset + len(table)))
            )
            attachment = attachment.append_column(
                "adjusted_reference_upper_kw", micro_to_decimal(adjusted, valid)
            )
            attachment = attachment.append_column(
                "scenario_status", pc.take(pa.array(STATUS), pa.array(status))
            )
            attachment = attachment.append_column(
                "availability_verified", pa.array(np.zeros(len(table), bool))
            )
            attachment = attachment.append_column(
                "boundary_assumption_applied", pa.array(boundary & valid)
            )
            attachment = attachment.append_column("exact_copy_references", pa.array(copies))
            writer = checked_writer(attachment, writer, path)
            offset += len(table)
        writer.close()
        if offset != 8468544 or status_counts.sum() != offset:
            raise ValueError("元行の脱落・増殖")
        if original_sums is None:
            original_sums = reference.copy()
        elif not np.array_equal(reference, original_sums):
            raise ValueError("シナリオ間で元容量が変化")
        q = {
            "scenario": label,
            "rows": offset,
            "status_counts": dict(zip(STATUS, status_counts.tolist(), strict=True)),
        }
        row_checks[label] = q
        has_value = counts["scenario_defined_count"] > 0
        fully_defined = (
            (counts["capacity_held_count"] == 0)
            & (counts["record_held_count"] == 0)
            & (counts["negative_upper_count"] == 0)
            & has_value
        )
        region = plan
        for key, arr in counts.items():
            region = region.append_column(key, pa.array(arr))
        region = region.append_column(
            "unobserved_union_label_count",
            pa.array(np.repeat(union_counts, HORIZON) - counts["observed_unit_count"]),
        )
        region = region.append_column(
            "reference_capacity_known_partial_mw",
            micro_to_decimal(reference, counts["reference_known_count"] > 0, mw=True),
        )
        region = region.append_column(
            "adjusted_upper_partial_mw", micro_to_decimal(total, has_value, mw=True)
        )
        region = region.append_column(
            "adjusted_upper_if_observed_scope_defined_mw",
            micro_to_decimal(total, fully_defined, mw=True),
        )
        region = region.append_column(
            "all_suppliers_lp_status", pa.array(["held_undefined_fence_and_scope"] * N)
        )
        region = region.append_column("supply_scenario", pa.array([label] * N))
        region = region.append_column("availability_verified", pa.array(np.zeros(N, bool)))
        region = region.append_column(
            "aggregation_scope", pa.array(["観測された参照対象の部分和。全地域供給力ではない"] * N)
        )
        region_writer = checked_writer(
            region, region_writer, output / "regional_all_suppliers.parquet"
        )
        first = plan.slice(0, HORIZON).select(
            ["delivery_date", "period", "timestamp_start", "timestamp_end", "timezone"]
        )
        ntotal = total.reshape(10, HORIZON)[:9].sum(axis=0)
        ndemand = demand.reshape(10, HORIZON)[:9].sum(axis=0)
        ndefined = counts["scenario_defined_count"].reshape(10, HORIZON)[:9].sum(axis=0)
        nation = first.append_column("planned_demand_nine_areas_mw", pa.array(ndemand))
        nation = nation.append_column(
            "adjusted_upper_partial_mw", micro_to_decimal(ntotal, ndefined > 0, mw=True)
        )
        for key, arr in counts.items():
            nation = nation.append_column(key, pa.array(arr.reshape(10, HORIZON)[:9].sum(axis=0)))
        nation = nation.append_column("supply_scenario", pa.array([label] * HORIZON))
        nation = nation.append_column(
            "all_suppliers_lp_status", pa.array(["held_undefined_fence_and_scope"] * HORIZON)
        )
        nation_writer = checked_writer(
            nation, nation_writer, output / "national_nine_areas_all_suppliers.parquet"
        )
        # 除去候補は停止調整済みの名参照分だけ。未知はnullで、元認可出力を再控除しない。
        for target in targets.itertuples():
            fid, ai = firms.index(target.regulatory_entity), AREAS.index(target.market_area)
            sl = slice(ai * HORIZON, (ai + 1) * HORIZON)
            global_held = firm_held[fid].reshape(10, HORIZON)[:9].sum(axis=0)
            global_neg = firm_negative[fid].reshape(10, HORIZON)[:9].sum(axis=0)
            available = named_global[fid] & (global_held == 0) & (global_neg == 0)
            removed = firm_sum[fid].reshape(10, HORIZON)[:9].sum(axis=0)
            f = first.append_column("area", pa.array([target.market_area] * HORIZON))
            f = f.append_column("regulatory_entity", pa.array([target.regulatory_entity] * HORIZON))
            f = f.append_column(
                "name_reference_removed_nine_areas_mw",
                micro_to_decimal(removed, available, mw=True),
            )
            f = f.append_column(
                "name_reference_removed_in_area_mw",
                micro_to_decimal(firm_sum[fid, sl], available, mw=True),
            )
            f = f.append_column(
                "remaining_known_partial_in_area_mw",
                micro_to_decimal(total[sl] - firm_sum[fid, sl], available & has_value[sl], mw=True),
            )
            if np.any(available & (firm_sum[fid, sl] < 0)):
                raise ValueError("負値の除去容量を自動採用しない")
            st = np.where(
                available,
                "diagnostic_adjusted_name_reference_only",
                "held_adjusted_supply_or_no_name_reference",
            )
            if not named_global[fid]:
                st[:] = "held_no_name_reference_not_zero_supply"
            f = f.append_column("removal_status", pa.array(st))
            f = f.append_column("supply_scenario", pa.array([label] * HORIZON))
            f = f.append_column("asset_attribution_verified", pa.array(np.zeros(HORIZON, bool)))
            firm_writer = checked_writer(
                f, firm_writer, output / "name_removal_capacity_candidates.parquet"
            )
        for ai, area in enumerate(AREAS[:9] + ["九地域計"]):
            if ai == 9:
                value, dem, defined = ntotal, ndemand, ndefined > 0
                agg = {k: v.reshape(10, HORIZON)[:9].sum(axis=0) for k, v in counts.items()}
            else:
                sl = slice(ai * HORIZON, (ai + 1) * HORIZON)
                value, dem, defined = total[sl], demand[sl], has_value[sl]
                agg = {k: v[sl] for k, v in counts.items()}
            numerical = value.astype(float) / 1000000000
            summaries.append(
                {
                    "scenario": label,
                    "area": area,
                    "time_cells": HORIZON,
                    "known_partial_below_demand_cells": int((defined & (numerical < dem)).sum()),
                    "minimum_known_partial_mw": float(numerical[defined].min())
                    if defined.any()
                    else None,
                    "maximum_known_partial_mw": float(numerical[defined].max())
                    if defined.any()
                    else None,
                    "minimum_plan_demand_mw": float(dem.min()),
                    "maximum_plan_demand_mw": float(dem.max()),
                    "capacity_held_unit_cells": int(agg["capacity_held_count"].sum()),
                    "record_held_unit_cells": int(agg["record_held_count"].sum()),
                    "negative_upper_unit_cells": int(agg["negative_upper_count"].sum()),
                    "stop_zero_unit_cells": int(agg["stop_zero_count"].sum()),
                    "mean_subtraction_unit_cells": int(agg["mean_subtraction_count"].sum()),
                    "no_record_no_deduction_unit_cells": int(
                        agg["record_absent_no_deduction_count"].sum()
                    ),
                    "generation_missing_unit_cells": int(agg["generation_missing_count"].sum()),
                    "network_feasibility_proven": False,
                }
            )
        all_status.append(q)
        print(label, offset, "行、調整後上限を保存", flush=True)
    for writer in (region_writer, nation_writer, firm_writer):
        writer.close()
    for item in inputs:
        if digest(root / item["path"]) != item["sha256"]:
            raise ValueError("入力SHA不変性違反")
    reports = root / "reports/16_saved_plan_supply/qa"
    pd.DataFrame(summaries).to_csv(reports / "all_suppliers_summary.csv", index=False)
    qa = {
        "purpose": "保存された翌々日計画資料による事後的な参照対象の限定診断",
        "inputs": inputs,
        "profiles": all_status,
        "network": network_qa,
        "actual_generation_subtracted": False,
        "availability_verified": False,
        "all_firm_real_network_feasibility_evaluated": False,
        "real_two_area_model_run": False,
        "rsi_computed": False,
        "join_unmatched_source_rows": 0,
        "row_multiplication_per_condition": 0,
        "source_rows_per_condition": 8468544,
        "regional_rows": N * len(profiles),
        "national_rows": HORIZON * len(profiles),
        "removal_candidate_rows": 11 * HORIZON * len(profiles),
        "outputs": [
            {"path": str(p.relative_to(root)), "sha256": digest(p), "bytes": p.stat().st_size}
            for p in sorted(output.rglob("*.parquet"))
        ],
    }
    (reports / "scenario_audit.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n"
    )
    return qa


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.output.resolve())
