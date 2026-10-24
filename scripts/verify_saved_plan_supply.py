"""新診断版を原本と独立照合し、保留にかかる参照容量を補足する。"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.diagnose_saved_plan_supply import N, ids  # noqa: E402
from scripts.inspect_rsi_inputs import digest  # noqa: E402
from src.saved_plan_supply import (  # noqa: E402
    HORIZON,
    STATUS,
    Edge,
    decimal_to_micro,
    evaluate_named_removal,
    micro_to_decimal,
    solve_transport,
)


def verify(root, output):
    report = root / "reports/16_saved_plan_supply/qa"
    qa = json.loads((report / "scenario_audit.json").read_text())
    if qa.get("independent_verification"):
        raise ValueError("検査済み診断版は上書きしない")
    source = root / next(r["path"] for r in qa["inputs"] if "generation_with_snapshot" in r["path"])
    contexts = pq.read_table(source.parent / "public_record_contexts.parquet").to_pylist()
    records = {
        r["content_id"]: r
        for r in pq.read_table(source.parent / "record_contents.parquet").to_pylist()
    }
    mean = np.zeros(len(contexts), np.int64)
    for c in contexts:
        ids_ = c["full_scenario_content_ids"]
        if len(ids_) == 1:
            value = records[ids_[0]]["published_mean_reduction_kw"]
            if value is not None and value.is_finite():
                mean[c["context_id"]] = int(value * 1000000)
    exposures, results, reports_status = [], [], {}
    writer = None
    shared = [
        "asset_key_candidate",
        "area",
        "delivery_date",
        "period",
        "capacity_reference_kw",
        "qa_generation_missing",
    ]
    for item in qa["profiles"]:
        label = item["scenario"]
        profile = None if label == "reference_no_deduction" else label.split("__")[0]
        path = output / "unit_scenarios" / (label + ".parquet")
        original = pq.ParquetFile(source).iter_batches(columns=shared, batch_size=65536)
        offset = 0
        held_capacity = np.zeros(N, np.int64)
        known = np.zeros(N, np.int32)
        status_counts = np.zeros(len(STATUS), np.int64)
        counts = np.zeros((2, len(STATUS)), np.int64)
        values = np.zeros((2, len(STATUS)), np.int64)
        held_by_area = np.zeros(10, np.int64)
        for batch in pq.ParquetFile(path).iter_batches(batch_size=65536):
            actual = pa.Table.from_batches([batch])
            expected = pa.Table.from_batches([next(original)])
            if not actual.select(shared).equals(expected):
                raise ValueError("原キー・原容量・元欠測flagが変化")
            if actual["source_observation_row"].to_pylist() != list(
                range(offset, offset + len(actual))
            ):
                raise ValueError("元観測の行増殖/脱落")
            if pc.any(actual["availability_verified"]).as_py():
                raise ValueError("利用可能性を認定したflagがある")
            cap, cap_valid = decimal_to_micro(actual["capacity_reference_kw"])
            adjusted, defined = decimal_to_micro(actual["adjusted_reference_upper_kw"])
            status = actual["scenario_status"].to_numpy()
            status_id = pc.index_in(
                actual["scenario_status"], value_set=pa.array(STATUS)
            ).to_numpy()
            status_counts += np.bincount(status_id, minlength=len(STATUS))
            deduction_free = np.isin(
                status,
                [
                    "reference_without_deduction",
                    "no_additional_deduction_record_absent",
                    "outside_interval_under_endpoint_scenario",
                ],
            )
            if not np.all(defined[deduction_free]) or not np.array_equal(
                adjusted[deduction_free], cap[deduction_free]
            ):
                raise ValueError("無控除の値が不一致")
            zero = status == "stop_upper_zero_under_scenario"
            if not np.all(defined[zero]) or np.any(adjusted[zero] != 0):
                raise ValueError("停止0シナリオ不一致")
            subtract = np.isin(
                status,
                ["subtract_published_mean_under_scenario", "negative_adjusted_upper_retained"],
            )
            if subtract.any():
                ctx = actual["pubrec_" + profile + "_context_id"].to_numpy()
                if not np.array_equal(adjusted[subtract], cap[subtract] - mean[ctx[subtract]]):
                    raise ValueError("公表平均の1回控除と不一致")
            held = np.array([s.startswith("held_") for s in status])
            if defined[held].any():
                raise ValueError("保留を数値へ補完")
            group, area, _ = ids(actual)
            selected = cap_valid & held
            np.add.at(held_capacity, group[selected], cap[selected])
            np.add.at(known, group[selected], 1)
            np.add.at(held_by_area, area[selected], cap[selected])
            for domain, mask in enumerate([np.ones(len(actual), bool), area < 9]):
                valid = mask & cap_valid
                np.add.at(counts[domain], status_id[mask], 1)
                np.add.at(values[domain], status_id[valid], cap[valid])
            offset += len(actual)
        if next(original, None) is not None or offset != 8468544:
            raise ValueError("原観測全件照合不一致")
        if dict(zip(STATUS, status_counts.tolist(), strict=True)) != item["status_counts"]:
            raise ValueError("保存QAと列内容不一致")
        plan_record = next(r for r in qa["inputs"] if "occto_reserve_nextday2" in r["path"])
        plan = pq.read_table(root / plan_record["path"])
        planids, _, _ = ids(plan)
        plan = pc.take(plan, pa.array(np.argsort(planids)))
        key = plan.select(
            ["area", "delivery_date", "period", "timestamp_start", "timestamp_end", "timezone"]
        )
        key = key.append_column("supply_scenario", pa.array([label] * N))
        # ここでの0は「保留容量の露出がない」。未知容量を0に置いた値ではない。
        key = key.append_column(
            "held_record_known_reference_capacity_mw", micro_to_decimal(held_capacity, mw=True)
        )
        key = key.append_column("held_record_with_known_capacity_count", pa.array(known))
        if writer is None:
            writer = pq.ParquetWriter(
                output / "held_record_capacity_exposure.parquet", key.schema, compression="zstd"
            )
        writer.write_table(key)
        for domain, domain_label in enumerate(["10地域原観測", "市場9地域"]):
            for sid, state in enumerate(STATUS):
                count = int(counts[domain, sid])
                if not count:
                    continue
                value = int(values[domain, sid])
                exposures.append(
                    {
                        "scenario": label,
                        "scope": domain_label,
                        "status": state,
                        "unit_cells": count,
                        "known_reference_capacity_kw_slot_sum": str(Decimal(value).scaleb(-6))
                        if value
                        else None,
                        "sum_unit": "kW・コマ方向和。時点容量・電力量ではない",
                    }
                )
        report_status = {
            "scenario": label,
            "source_rows_verified": offset,
            "known_reference_capacity_of_held_records_mw_slot_sum_nine_areas": str(
                Decimal(int(held_by_area[:9].sum())).scaleb(-9)
            ),
            "known_reference_capacity_of_held_records_time_mean_mw_nine_areas": float(
                held_by_area[:9].sum()
            )
            / 1e9
            / HORIZON,
            "held_reason_counts": {
                s: int(status_counts[i]) for i, s in enumerate(STATUS) if s.startswith("held_")
            },
        }
        results.append(report_status)
        reports_status[label] = report_status
        print(label, "原キー・原容量・調整式・null全件照合", flush=True)
    writer.close()
    pd.DataFrame(exposures).to_csv(report / "state_capacity_exposure.csv", index=False)
    pd.DataFrame(
        [{k: v for k, v in r.items() if k != "held_reason_counts"} for r in results]
    ).to_csv(report / "held_capacity_summary.csv", index=False)
    # 早端/遅端およびsnapshot差は、同じ観測行同士で比較する。
    comparisons = []
    for strategy in ["full_stop_zero", "mean_reduction"]:
        pairs = [
            (f"{n}_early__{strategy}", f"{n}_late__{strategy}", "端点差") for n in ["58852", "7828"]
        ]
        pairs += [
            (f"58852_{end}__{strategy}", f"7828_{end}__{strategy}", "snapshot差")
            for end in ["early", "late"]
        ]
        for left, right, kind in pairs:
            count_status = count_null = count_value = 0
            difference = 0
            for a, b in zip(
                pq.ParquetFile(output / "unit_scenarios" / (left + ".parquet")).iter_batches(
                    columns=["adjusted_reference_upper_kw", "scenario_status"], batch_size=65536
                ),
                pq.ParquetFile(output / "unit_scenarios" / (right + ".parquet")).iter_batches(
                    columns=["adjusted_reference_upper_kw", "scenario_status"], batch_size=65536
                ),
                strict=True,
            ):
                av, ak = decimal_to_micro(a.column(0))
                bv, bk = decimal_to_micro(b.column(0))
                count_status += int(
                    np.sum(
                        a.column(1).to_numpy(zero_copy_only=False)
                        != b.column(1).to_numpy(zero_copy_only=False)
                    )
                )
                count_null += int(np.sum(ak != bk))
                common = ak & bk
                changed = common & (av != bv)
                count_value += int(changed.sum())
                difference += int(np.abs(av[changed] - bv[changed]).sum())
            comparisons.append(
                {
                    "comparison": kind,
                    "left": left,
                    "right": right,
                    "status_changed_unit_cells": count_status,
                    "definedness_changed_unit_cells": count_null,
                    "numeric_changed_where_both_defined_unit_cells": count_value,
                    "absolute_kw_slot_sum_where_both_defined": str(Decimal(difference).scaleb(-6)),
                }
            )
    pd.DataFrame(comparisons).to_csv(report / "snapshot_endpoint_comparisons.csv", index=False)
    # 元計画の全41列・全セル・型を地域表から独立照合。
    region = pq.ParquetFile(output / "regional_all_suppliers.parquet")
    regional = pq.read_table(output / "regional_all_suppliers.parquet", columns=plan.column_names)
    for i in range(9):
        if not regional.slice(i * N, N).equals(plan):
            raise ValueError("元の需要計画列が変更")
    network_record = next(
        r for r in qa["inputs"] if "system_capacity_dayahead2_planned_direction" in r["path"]
    )
    original_network = pq.read_table(root / network_record["path"])
    if not pq.read_table(
        output / "network_candidate_bounds.parquet", columns=original_network.column_names
    ).equals(original_network):
        raise ValueError("元の系統列が変更")
    # 候補の除去容量も未知・負値をチェック。EP・ミライズの参照なしは常にnull。
    firm = pq.read_table(
        output / "name_removal_capacity_candidates.parquet",
        columns=[
            "area",
            "regulatory_entity",
            "delivery_date",
            "period",
            "supply_scenario",
            "name_reference_removed_nine_areas_mw",
            "name_reference_removed_in_area_mw",
            "remaining_known_partial_in_area_mw",
            "removal_status",
            "asset_attribution_verified",
        ],
    ).to_pandas()
    if firm.duplicated(
        ["area", "regulatory_entity", "delivery_date", "period", "supply_scenario"]
    ).any():
        raise ValueError("除去候補のキー重複")
    missing = firm.regulatory_entity.isin(
        ["東京電力エナジーパートナー株式会社", "中部電力ミライズ株式会社"]
    )
    for column in [
        "name_reference_removed_nine_areas_mw",
        "name_reference_removed_in_area_mw",
        "remaining_known_partial_in_area_mw",
    ]:
        if firm.loc[missing, column].notna().any():
            raise ValueError("無参照を供給0にした")
    if firm.asset_attribution_verified.any():
        raise ValueError("法人帰属を確定した")
    firm.groupby(
        ["supply_scenario", "regulatory_entity", "area", "removal_status"], observed=True
    ).size().rename("time_cells").reset_index().to_csv(
        report / "name_removal_readiness.csv", index=False
    )
    synthetic = {
        "two_area_baseline_then_removed": evaluate_named_removal(
            {"A": 100, "B": 100},
            [
                {"area": "A", "entity_reference": "i", "adjusted_upper_mw": 40},
                {"area": "A", "entity_reference": "j", "adjusted_upper_mw": 120},
                {"area": "B", "entity_reference": "i", "adjusted_upper_mw": 60},
            ],
            "i",
            [Edge("A", "B", 60, 0)],
        ),
        "sender_demand_limits_target_ratio": solve_transport(
            {"A": 100, "B": 100}, {"A": 160, "B": 0}, [Edge("A", "B", 60, 0)], target="B"
        ),
        "role": "すべて人工数値。北海道・東北の実態評価ではない",
    }
    (report / "synthetic_lp_examples.json").write_text(
        json.dumps(synthetic, ensure_ascii=False, indent=2) + "\n"
    )
    qa["independent_verification"] = {
        "source_attachment_rows_per_condition": 8468544,
        "conditions": 9,
        "source_key_capacity_and_missing_flag_all_cells_equal": True,
        "adjustment_formula_and_null_all_cells_verified": True,
        "plan_all_columns_and_types_preserved": True,
        "network_all_columns_and_types_preserved": True,
        "firm_key_duplicate_count": 0,
        "EP_miraiz_unknowns_preserved": True,
        "regional_rows": region.metadata.num_rows,
        "real_network_lp_run": False,
    }
    qa["outputs"] = [
        {"path": str(p.relative_to(root)), "sha256": digest(p), "bytes": p.stat().st_size}
        for p in sorted(output.rglob("*.parquet"))
    ]
    for item in qa["inputs"]:
        if digest(root / item["path"]) != item["sha256"]:
            raise ValueError("元入力SHA変更")
    (report / "scenario_audit.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
    print("独立照合・保留容量露出・snapshot/端点差を保存")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(Path(__file__).resolve().parents[1], args.output.resolve())
