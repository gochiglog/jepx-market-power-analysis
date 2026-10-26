"""4月、固定Rを残す企業名参照代理の限定試行。既存版は変更しない。"""

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.adjustable_removal import maximum_multiplier, trial_upper  # noqa: E402
from src.april_balance import solve_balance  # noqa: E402
from src.net_demand_network import AREAS, EDGES  # noqa: E402

ENTITIES = {
    "hokkaido": "北海道電力株式会社",
    "tohoku": "東北電力株式会社",
    "jera": "株式会社JERA",
    "hokuriku": "北陸電力株式会社",
    "kansai": "関西電力株式会社",
    "chugoku": "中国電力株式会社",
    "shikoku": "四国電力株式会社",
    "kyushu": "九州電力株式会社",
}
TARGETS = [
    ("北海道", "hokkaido"),
    ("東北", "tohoku"),
    ("東京", "jera"),
    ("中部", "jera"),
    ("北陸", "hokuriku"),
    ("関西", "kansai"),
    ("中国", "chugoku"),
    ("四国", "shikoku"),
    ("九州", "kyushu"),
]
KEY = ["area", "delivery_date", "period"]
APRIL = [("delivery_date", ">=", date(2025, 4, 1)), ("delivery_date", "<=", date(2025, 4, 30))]


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(root, output):
    if output.exists():
        raise ValueError("既存出力先は禁止")
    output.mkdir(parents=True)
    (output / "unit_trial_uppers").mkdir()
    qa = root / "reports/21_adjustable_removal/qa"
    qa.mkdir(parents=True, exist_ok=True)
    inputs, outputs = {}, []

    def track(path):
        inputs[str(path.relative_to(root))] = sha(path)
        return path

    def read(path, **kwargs):
        return pq.read_table(track(path), **kwargs).to_pandas()

    def write(frame, name):
        path = output / (name + ".parquet")
        frame = frame.copy()
        for column in frame.select_dtypes(include=["category"]):
            frame[column] = frame[column].astype(str)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, path, compression="zstd")
        if not pq.read_table(path).equals(table):
            raise ValueError("Parquet再読込不一致")
        outputs.append(
            {"path": str(path.relative_to(root)), "sha256": sha(path), "rows": len(frame)}
        )

    role = pd.read_csv(
        track(root / "reports/19_actual_net_demand/qa/fixed_adjustable_role_candidates.csv"),
        keep_default_na=False,
    )
    business = pd.read_csv(
        track(root / "reports/11_decision_preparation/qa/unit_business_inventory.csv"),
        keep_default_na=False,
    )
    aliases = pd.read_csv(
        track(root / "metadata/research/legal_name_alias_evidence_20261016.csv"),
        keep_default_na=False,
    )
    lookup = dict(zip(aliases.alias_raw, aliases.canonical_legal_name, strict=True))
    registry = role.merge(
        business[
            [
                "asset_key_candidate",
                "hjks_business_name_unique_reference",
                "hjks_business_name_reference_status",
                "hjks_candidate_rows_json",
                "snapshot_source_file",
                "snapshot_source_sha256",
                "fy2025_asset_business_relationship_verified",
                "owner_role_verified",
                "seller_role_verified",
                "contracted_supply_role_verified",
            ]
        ],
        on="asset_key_candidate",
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    if len(registry) != 491 or not registry._merge.eq("both").all():
        raise ValueError("設備/事業者台帳join不一致")
    registry["business_name_canonical"] = registry.hjks_business_name_unique_reference.map(
        lambda x: lookup.get(x, x)
    )
    registry["eligible_adjustable_candidate"] = registry.candidate_analysis_role.eq(
        "adjustable_reference_candidate"
    ) & registry.area.isin(AREAS)
    registry["name_proxy_defined"] = (
        registry.eligible_adjustable_candidate
        & registry.hjks_business_name_reference_status.eq("scope_checked_unique_reference")
    )
    registry["removal_entity_code"] = registry.business_name_canonical.map(
        {v: k for k, v in ENTITIES.items()}
    ).fillna("")
    registry.loc[~registry.name_proxy_defined, "removal_entity_code"] = ""
    registry["attribution_proxy"] = (
        "点検済み対応先HJKSの発電事業者名。所有・支配・契約の確定ではない"
    )
    registry["jera_general_succession_evidence"] = (
        "2019-koukoku.pdf 物理9、2019-04-01の対象事業承継（個別権利を確定しない）"
    )
    registry["subsidiary_joint_contract_unverified"] = True
    registry["holding_ratio_allocation_applied"] = False
    registry["final_attribution_assigned"] = False
    if registry[registry.name_proxy_defined].asset_key_candidate.duplicated().any():
        raise ValueError("重複設備代理")
    assert registry.eligible_adjustable_candidate.sum() == 372
    assert (
        not registry.loc[registry.eligible_adjustable_candidate, "candidate_analysis_role"]
        .eq("fixed_observed_nuclear_candidate")
        .any()
    )
    registry.to_csv(qa / "asset_attribution_proxy.csv", index=False)
    write(registry, "asset_attribution_proxy")
    target_list = pd.read_csv(track(root / "metadata/research/regulatory_targets.csv"))
    assert len(target_list) == 11 and target_list.regulatory_entity.nunique() == 10
    target_list["initial_trial_included"] = [
        (a, e) in [(a, ENTITIES[k]) for a, k in TARGETS]
        for a, e in zip(target_list.market_area, target_list.regulatory_entity, strict=True)
    ]
    target_list["trial_status"] = np.where(
        target_list.initial_trial_included,
        "name_proxy_trial",
        "attribution_unknown_contract_supply_not_evaluated",
    )
    target_list.to_csv(qa / "unchanged_target_list_trial_scope.csv", index=False)
    data = read(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    )
    data = data.set_index(KEY)
    assert len(data) == 12960 and data.index.is_unique
    network = read(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet",
        filters=APRIL,
    )
    network.delivery_date = pd.to_datetime(network.delivery_date).dt.date
    groups = {(d, p): g for (d, p), g in network.groupby(["delivery_date", "period"], sort=True)}
    assert len(groups) == 1440 and all(len(g) == 11 for g in groups.values())
    incidence = np.zeros((9, 10))
    for i, (_, sender, receiver) in enumerate(EDGES):
        incidence[AREAS.index(sender), i] = -1
        incidence[AREAS.index(receiver), i] = 1
    # 係数列順はEDGES、法人数8・評価地域9。JERAは同じ除去ベクトルを2地域で評価。
    defined_entities = set(registry.loc[registry.name_proxy_defined, "removal_entity_code"])
    ids = set(registry.loc[registry.eligible_adjustable_candidate, "asset_key_candidate"])
    entity_map = registry.set_index("asset_key_candidate").removal_entity_code.to_dict()
    result_rows, regional_rows, restoration_rows, sample_rows = [], [], [], []
    base_cache, max_cache = {}, {}
    sampled = set()
    scenarios = root / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1/unit_scenarios"
    for path in sorted(scenarios.glob("*.parquet")):
        original = read(path, filters=APRIL)
        for restore in [False] if path.stem == "reference_no_deduction" else [False, True]:
            name = path.stem + ("__record_held_reference" if restore else "")
            print("start", name, flush=True)
            unit = original.copy()
            unit["eligible_adjustable_candidate"] = unit.asset_key_candidate.isin(ids)
            unit["removal_entity_code"] = unit.asset_key_candidate.map(entity_map).fillna("")
            calculated = [
                trial_upper(o, r, s, restore_record_held=restore) if eligible else (None, False)
                for o, r, s, eligible in zip(
                    unit.adjusted_reference_upper_kw,
                    unit.capacity_reference_kw,
                    unit.scenario_status,
                    unit.eligible_adjustable_candidate,
                    strict=True,
                )
            ]
            unit["trial_adjustable_upper_kw"] = [v[0] for v in calculated]
            unit["record_held_reference_restored"] = [v[1] for v in calculated]
            unit["trial_upper_defined"] = unit.trial_adjustable_upper_kw.map(
                lambda x: x is not None and x >= 0
            )
            unit["supply_trial_scenario"] = name
            # 元701280行と全元列を保持。調整集合外はtrial値null、原上限は残す。
            assert len(unit) == 701280 and not unit.source_observation_row.duplicated().any()
            write(unit, "unit_trial_uppers/" + name)
            selected = unit[unit.eligible_adjustable_candidate]
            if selected.duplicated(["asset_key_candidate", "delivery_date", "period"]).any():
                raise ValueError("元unit時刻重複")
            current = []
            for (area, day, period), g in selected.groupby(KEY, sort=True):
                defined = g[g.trial_upper_defined]
                entry = {
                    "area": area,
                    "delivery_date": day,
                    "period": int(period),
                    "supply_trial_scenario": name,
                    "original_scenario": path.stem,
                    "record_held_reference_scenario": restore,
                    "upper_limited_mw": sum(defined.trial_adjustable_upper_kw, Decimal(0))
                    / Decimal(1000),
                    "observed_candidate_count": len(g),
                    "defined_candidate_count": len(defined),
                    "held_candidate_count": len(g) - len(defined),
                    "restored_candidate_count": int(g.record_held_reference_restored.sum()),
                    "restored_reference_mw": sum(
                        g.loc[g.record_held_reference_restored, "capacity_reference_kw"], Decimal(0)
                    )
                    / Decimal(1000),
                    "unknown_upper_is_zero": False,
                }
                for entity in ENTITIES:
                    entry["remove_" + entity + "_mw"] = sum(
                        defined.loc[
                            defined.removal_entity_code.eq(entity), "trial_adjustable_upper_kw"
                        ],
                        Decimal(0),
                    ) / Decimal(1000)
                current.append(entry)
            reg = pd.DataFrame(current)
            assert len(reg) == 12960
            regional_rows.append(reg)
            restoration_rows.append(
                {
                    "scenario": name,
                    "restored_unit_cells": int(selected.record_held_reference_restored.sum()),
                    "held_unit_cells": int((~selected.trial_upper_defined).sum()),
                    "negative_upper_cells_retained": int(
                        selected.trial_adjustable_upper_kw.map(
                            lambda x: x is not None and x < 0
                        ).sum()
                    ),
                }
            )
            reg = reg.set_index(KEY)
            before = len(result_rows)
            for (day, period), ng in groups.items():
                order = [(a, day, period) for a in AREAS]
                net = data.loc[order].net_demand_B_mw.to_numpy(float)
                region_slice = reg.loc[order]
                u = region_slice.upper_limited_mw.to_numpy(float)
                coeff = np.array([json.loads(x) for x in ng.coefficients_json], float)
                lo, hi = ng.lower_mw.to_numpy(float), ng.upper_mw.to_numpy(float)
                for constrained in (True, False):
                    network_case = (
                        "saved_plan_constraints" if constrained else "no_transmission_bounds"
                    )
                    bound_key = tuple(np.r_[lo, hi]) if constrained else ()
                    full_key = (tuple(net), tuple(u), bound_key)
                    if full_key not in base_cache:
                        if constrained:
                            b = solve_balance(net, u, incidence, coeff, lo, hi)
                            base_status = (
                                "feasible"
                                if b["status"] == "feasible"
                                else "infeasible"
                                if b["status"] == "infeasible"
                                else "numerical_failure"
                            )
                        else:
                            base_status = "feasible" if 0 <= net.sum() <= u.sum() else "infeasible"
                        base_cache[full_key] = base_status
                    base_status = base_cache[full_key]
                    for area, entity in TARGETS:
                        target = AREAS.index(area)
                        removal = region_slice["remove_" + entity + "_mw"].to_numpy(float)
                        remaining = u - removal
                        if (remaining < -1e-8).any():
                            raise ValueError("除去量が調整済み上限を超える")
                        # Decimal部分和を差し引いてからfloatへ変換。負値をクリップしない。
                        remaining = (
                            region_slice.upper_limited_mw - region_slice["remove_" + entity + "_mw"]
                        ).to_numpy(float)
                        entry = {
                            "supply_trial_scenario": name,
                            "delivery_date": day,
                            "period": int(period),
                            "target_area": area,
                            "entity": ENTITIES[entity],
                            "entity_code": entity,
                            "network_scenario": network_case,
                            "all_firms_balance_status": base_status,
                            "target_net_demand_mw": float(net[target]),
                            "removed_adjustable_mw_all_nine_areas": float(removal.sum()),
                            "removed_fixed_output_mw": 0.0,
                            "held_candidate_cells": int(region_slice.held_candidate_count.sum()),
                            "restored_candidate_cells": int(
                                region_slice.restored_candidate_count.sum()
                            ),
                            "attribution_proxy": "scope_checked_HJKS_business_name",
                            "lambda_all_firms": None,
                            "lambda_removed": None,
                            "all_firms_multiplier_status": None,
                            "removed_multiplier_status": None,
                        }
                        if net[target] <= 0:
                            entry["status"] = "nonpositive_target_net"
                        elif base_status == "infeasible":
                            entry["status"] = "all_firms_infeasible"
                        elif base_status != "feasible":
                            entry["status"] = "numerical_failure"
                        elif entity not in defined_entities:
                            entry["status"] = "attribution_unknown"
                        else:
                            for phase, up in [("all_firms", u), ("removed", remaining)]:
                                key = (tuple(net), tuple(up), target, bound_key)
                                if key not in max_cache:
                                    answer = maximum_multiplier(
                                        net,
                                        up,
                                        target,
                                        incidence,
                                        coeff,
                                        lo,
                                        hi,
                                        constrained=constrained,
                                    )
                                    sample_key = (name, area, network_case, phase)
                                    if (
                                        answer["status"] == "valid"
                                        and constrained
                                        and sample_key not in sampled
                                    ):
                                        sample_rows.append(
                                            {
                                                "supply_trial_scenario": name,
                                                "target_area": area,
                                                "phase": phase,
                                                "delivery_date": day,
                                                "period": int(period),
                                                "multiplier": answer["multiplier"],
                                                "upper_mw_json": json.dumps(up.tolist()),
                                                "generation_mw_json": json.dumps(
                                                    answer["generation_mw"]
                                                ),
                                                "flows_mw_json": json.dumps(answer["flows_mw"]),
                                            }
                                        )
                                        sampled.add(sample_key)
                                    max_cache[key] = {
                                        k: v for k, v in answer.items() if not isinstance(v, list)
                                    }
                                answer = max_cache[key]
                                entry["lambda_" + phase] = answer["multiplier"]
                                entry[phase + "_multiplier_status"] = answer["status"]
                                entry[phase + "_max_residual_mw"] = answer.get("residual_mw")
                            entry["status"] = (
                                "valid"
                                if entry["removed_multiplier_status"] == "valid"
                                else "removed_other_regions_infeasible"
                                if entry["removed_multiplier_status"] == "other_regions_infeasible"
                                else entry["removed_multiplier_status"]
                            )
                            if (
                                entry["status"] == "valid"
                                and entry["lambda_removed"] > entry["lambda_all_firms"] + 1e-7
                            ):
                                raise ValueError("企業除去で最大倍率が増加")
                        result_rows.append(entry)
            counts = pd.Series([r["status"] for r in result_rows[before:]]).value_counts().to_dict()
            print("done", name, counts, flush=True)
    results = pd.DataFrame(result_rows)
    assert len(results) == 17 * 1440 * 9 * 2
    assert not results.duplicated(
        ["supply_trial_scenario", "delivery_date", "period", "target_area", "network_scenario"]
    ).any()
    write(pd.concat(regional_rows, ignore_index=True), "regional_trial_uppers")
    write(results, "all_and_removed_net_multipliers")
    write(pd.DataFrame(sample_rows), "multiplier_solution_samples")
    pd.DataFrame(restoration_rows).to_csv(qa / "record_held_reference_scope.csv", index=False)
    keys = ["supply_trial_scenario", "target_area", "entity", "network_scenario"]
    results.groupby(keys + ["status"]).size().rename("count").reset_index().to_csv(
        qa / "status_counts.csv", index=False
    )
    summary = []
    for k, g in results.groupby(keys):
        valid = g[g.status.eq("valid")]
        summary.append(
            dict(zip(keys, k, strict=True))
            | {
                "total": len(g),
                "n_valid": len(valid),
                "n_lt1": int((valid.lambda_removed < 1 - 1e-8).sum()),
                "n_near1_numerical_band": int((valid.lambda_removed.sub(1).abs() <= 1e-8).sum()),
                "lambda_removed_min": valid.lambda_removed.min(),
                "lambda_removed_median": valid.lambda_removed.median(),
                "lambda_removed_max": valid.lambda_removed.max(),
                "lt1_share_among_valid": None
                if not len(valid)
                else float((valid.lambda_removed < 1 - 1e-8).mean()),
            }
        )
    pd.DataFrame(summary).to_csv(qa / "multiplier_summary.csv", index=False)
    # 同じ入力・共通有効時刻だけを比べる。保留行を数値0へ置換しない。
    pair_keys = ["supply_trial_scenario", "delivery_date", "period", "target_area", "entity"]
    paired = results[results.network_scenario.eq("saved_plan_constraints")].merge(
        results[results.network_scenario.eq("no_transmission_bounds")],
        on=pair_keys,
        how="outer",
        validate="one_to_one",
        indicator=True,
        suffixes=("_network", "_relaxed"),
    )
    assert paired._merge.eq("both").all() and len(paired) == 17 * 1440 * 9
    paired["common_valid"] = paired.status_network.eq("valid") & paired.status_relaxed.eq("valid")
    write(paired, "network_comparison_common_times")
    compare = []
    for k, g in paired.groupby(["supply_trial_scenario", "target_area", "entity"]):
        valid = g[g.common_valid]
        assert (valid.lambda_removed_network <= valid.lambda_removed_relaxed + 1e-7).all()
        compare.append(
            dict(zip(["supply_trial_scenario", "target_area", "entity"], k, strict=True))
            | {
                "common_valid": len(valid),
                "network_lt1": int((valid.lambda_removed_network < 1 - 1e-8).sum()),
                "relaxed_lt1": int((valid.lambda_removed_relaxed < 1 - 1e-8).sum()),
                "median_network_lambda": valid.lambda_removed_network.median(),
                "median_relaxed_lambda": valid.lambda_removed_relaxed.median(),
            }
        )
    pd.DataFrame(compare).to_csv(qa / "common_time_comparison.csv", index=False)
    for p, h in inputs.items():
        if sha(root / p) != h:
            raise ValueError("入力変更")
    audit = {
        "version": "20261027_adjustable_removal_v1",
        "result_rows": len(results),
        "scenarios": 17,
        "time_keys": 1440,
        "initial_target_pairs": 9,
        "initial_legal_entities": 8,
        "institutional_entities_unchanged": 10,
        "institutional_pairs_unchanged": 11,
        "asset_proxy_join_rate": 1.0,
        "asset_proxy_unmatched": 0,
        "duplicate_keys": 0,
        "fixed_output_removed": False,
        "realized_generation_subtracted": False,
        "all_nine_areas_connected": True,
        "unknown_capacity_assumed_zero": False,
        "silent_drop": 0,
        "network_comparison_join_rate": 1.0,
        "network_comparison_unmatched": 0,
        "network_constraint_count": 11,
        "numerical_lt1_reporting_band": 1e-8,
        "inputs": [{"path": p, "sha256": h} for p, h in inputs.items()],
        "outputs": outputs,
    }
    (qa / "run_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.output.resolve())
