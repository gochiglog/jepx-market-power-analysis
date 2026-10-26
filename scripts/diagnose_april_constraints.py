"""工程23：17条件内の一致、11組解除、供給集合と代表時刻。既存版は不変。"""

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.trial_adjustable_removal import ENTITIES  # noqa: E402
from src.adjustable_removal import RECORD_HELD, maximum_multiplier  # noqa: E402
from src.april_constraint_diagnosis import (  # noqa: E402
    condition_consistency,
    decompose_supply_scope,
    diagnostic_slack,
    pair_simultaneous_conditions,
    representative_boundary_checks,
    select_constraints,
)
from src.april_trial_comparison import simultaneous_feasibility  # noqa: E402
from src.net_demand_network import AREAS, EDGES  # noqa: E402

SOURCE = Path("data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3")
COMPARISON = Path("data/interim/occto/april_trial_comparison/20261027_comparison_v1")
FOCUS = ("hokkaido", "hokuriku", "jera", "kyushu")
TARGETS = [
    ("北海道", "hokkaido"),
    ("北陸", "hokuriku"),
    ("東京", "jera"),
    ("中部", "jera"),
    ("九州", "kyushu"),
]
REF = "reference_no_deduction"
KEY = ["area", "delivery_date", "period"]


def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(root, output, report):
    if output.exists() or (report / "qa/run_audit.json").exists():
        raise ValueError("既存版への上書き禁止")
    output.mkdir(parents=True)
    qa = report / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    inputs, outputs = {}, []

    def read(path, **kwargs):
        inputs[str(path.relative_to(root))] = sha(path)
        return pq.read_table(path, **kwargs).to_pandas()

    def write(f, name):
        path = output / (name + ".parquet")
        f = f.copy()
        for col in f.select_dtypes(include="category"):
            f[col] = f[col].astype(str)
        t = pa.Table.from_pandas(f, preserve_index=False)
        pq.write_table(t, path, compression="zstd")
        assert pq.read_table(path).equals(t)
        outputs.append(dict(path=str(path.relative_to(root)), sha256=sha(path), rows=len(f)))

    sim = read(root / COMPARISON / "nine_area_simultaneous_after_removal.parquet")
    original_sim_columns = sim.columns.tolist()
    r = read(root / SOURCE / "all_and_removed_net_multipliers.parquet")
    regional = read(root / SOURCE / "regional_trial_uppers.parquet")
    primary = read(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    )
    network = read(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet",
        filters=[
            ("delivery_date", ">=", date(2025, 4, 1)),
            ("delivery_date", "<=", date(2025, 4, 30)),
        ],
    )
    assert (
        len(sim) == 391680
        and len(r) == 440640
        and len(regional) == 220320
        and len(primary) == 12960
    )
    # 全17条件での排他的区分と、重複する比較可能範囲のflag。
    rows = []
    for key, g in sim.groupby(
        ["network_scenario", "entity_code", "delivery_date", "period"], sort=False
    ):
        rows.append(
            dict(zip(["network_scenario", "entity_code", "delivery_date", "period"], key))
            | condition_consistency(g)
        )
    consistent = pd.DataFrame(rows)
    assert len(consistent) == 2 * 8 * 1440
    write(consistent, "condition_consistency_all_times")
    consistent.groupby(["network_scenario", "entity_code", "consistency_state"]).size().rename(
        "time_keys"
    ).reset_index().to_csv(qa / "condition_consistency_counts.csv", index=False)
    consistent.groupby(["network_scenario", "entity_code"]).agg(
        time_keys=("period", "size"),
        comparable_variation_times=("removed_varies_among_comparable", "sum"),
        all17_baseline_infeasible=("all17_baseline_infeasible", "sum"),
    ).reset_index().to_csv(qa / "overlapping_consistency_flags.csv", index=False)
    sim.groupby(
        ["network_scenario", "supply_trial_scenario", "entity_code", "transition"]
    ).size().rename("time_keys").reset_index().to_csv(
        qa / "all17_simultaneous_summary.csv", index=False
    )
    pairs = pair_simultaneous_conditions(sim)
    assert len(pairs) == 8 * 8 * 2 * 1440
    write(pairs, "paired_simultaneous_all_times")
    pairs.groupby(["original_scenario", "network_scenario", "entity_code"]).agg(
        time_keys=("period", "size"),
        both_baseline_feasible=("both_baseline_feasible", "sum"),
        removed_failure_to_feasible_comparable=("removed_failure_to_feasible_comparable", "sum"),
        baseline_became_feasible=("baseline_became_feasible", "sum"),
    ).reset_index().to_csv(qa / "stop_pair_simultaneous_summary.csv", index=False)
    # 既知上限の全国会計。不明供給を0とはしない。
    numeric = ["upper_limited_mw"] + ["remove_" + c + "_mw" for c in ENTITIES]
    assert not regional[numeric].isna().any().any()
    national = (
        regional.groupby(["supply_trial_scenario", "delivery_date", "period"])[numeric]
        .sum()
        .reset_index()
    )
    nettotal = (
        primary.groupby(["delivery_date", "period"])
        .net_demand_B_mw.sum()
        .rename("national_net_mw")
        .reset_index()
    )
    national = national.merge(
        nettotal, on=["delivery_date", "period"], how="left", validate="many_to_one", indicator=True
    )
    assert national._merge.eq("both").all()
    national = national.drop(columns="_merge")
    enriched = sim.merge(
        national,
        on=["supply_trial_scenario", "delivery_date", "period"],
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    assert enriched._merge.eq("both").all()
    enriched["remaining_known_upper_mw"] = [
        float(u) - float(v)
        for u, v in zip(
            enriched.upper_limited_mw,
            [row["remove_" + row["entity_code"] + "_mw"] for row in enriched.to_dict("records")],
            strict=True,
        )
    ]
    enriched["national_known_upper_shortfall_mw"] = (
        enriched.national_net_mw.astype(float) - enriched.remaining_known_upper_mw
    ).clip(lower=0)
    enriched["national_fixed_surplus_mw"] = (-enriched.national_net_mw.astype(float)).clip(lower=0)
    enriched["input_diagnosis"] = "feasible_in_limited_set"
    bad = enriched.removed_simultaneous_status.eq("infeasible")
    enriched.loc[bad, "input_diagnosis"] = "regional_constraints_or_local_absorption"
    enriched.loc[bad & enriched.national_known_upper_shortfall_mw.gt(1e-6), "input_diagnosis"] = (
        "national_limited_upper_shortfall"
    )
    enriched.loc[bad & enriched.national_fixed_surplus_mw.gt(1e-6), "input_diagnosis"] = (
        "national_fixed_output_absorption_shortfall"
    )
    enriched.loc[
        ~enriched.removed_simultaneous_status.isin(["feasible", "infeasible"]), "input_diagnosis"
    ] = "input_or_solver_held"
    write(
        enriched[
            original_sim_columns
            + [
                "national_net_mw",
                "remaining_known_upper_mw",
                "national_known_upper_shortfall_mw",
                "national_fixed_surplus_mw",
                "input_diagnosis",
            ]
        ],
        "simultaneous_input_scope_diagnosis",
    )
    enriched.groupby(
        [
            "supply_trial_scenario",
            "network_scenario",
            "entity_code",
            "all_firms_status",
            "input_diagnosis",
        ]
    ).size().rename("time_keys").reset_index().to_csv(
        qa / "supply_network_diagnosis_counts.csv", index=False
    )
    # 11組解除。北陸の個別2辺とフェンスの既知包含群だけを追加。
    names = [x[0] for x in EDGES] + ["北陸フェンス"]
    assert network.groupby("constraint_name").coefficients_json.nunique().eq(1).all()
    release = {
        "saved_all": [],
        **{"release_" + str(i + 1).zfill(2): [n] for i, n in enumerate(names)},
        "release_hokuriku_overlap_group": [names[4], names[5], names[10]],
        "release_all": names,
    }
    registry = []
    for case, removed in release.items():
        registry.append(
            {
                "release_case": case,
                "released_constraints_json": json.dumps(removed, ensure_ascii=False),
                "number_of_released_pairs": len(removed),
                "connected_nine_areas_preserved": True,
                "release_both_lower_and_upper": True,
            }
        )
    pd.DataFrame(registry).to_csv(qa / "release_registry.csv", index=False)
    detail = []
    for i, n in enumerate(names):
        g = network[network.constraint_name.eq(n)]
        detail.append(
            {
                "constraint_name": n,
                "constraint_index": i,
                "coefficients_json": g.coefficients_json.iloc[0],
                "positive_orientation": f"{EDGES[i][1]}→{EDGES[i][2]}"
                if i < 10
                else "南福光順方向−越前順方向（中部→北陸＋関西→北陸）",
                "negative_orientation": f"{EDGES[i][2]}→{EDGES[i][1]}"
                if i < 10
                else "上記の逆向き組合せ",
                "scope": "個別区間" if i < 10 else "個別2辺への同時フェンス",
                "first_delivery_date": str(g.delivery_date.min()),
                "last_delivery_date": str(g.delivery_date.max()),
                "lower_mw_min": float(g.lower_mw.min()),
                "upper_mw_max": float(g.upper_mw.max()),
                "source": "metadata/research/net_demand_network_sources_20261026.yml",
            }
        )
    pd.DataFrame(detail).to_csv(qa / "constraint_direction_scope.csv", index=False)
    inc = np.zeros((9, 10))
    for i, (_, s, t) in enumerate(EDGES):
        inc[AREAS.index(s), i] = -1
        inc[AREAS.index(t), i] = 1
    ngroups = {
        (d, p): g.set_index("constraint_name").loc[names]
        for (d, p), g in network.groupby(["delivery_date", "period"])
    }
    rg = regional[regional.supply_trial_scenario.eq(REF)].set_index(KEY)
    pg = primary.set_index(KEY)
    fullold = sim[sim.supply_trial_scenario.eq(REF)].set_index(
        ["network_scenario", "entity_code", "delivery_date", "period"]
    )
    rold = r[r.supply_trial_scenario.eq(REF)].set_index(
        ["network_scenario", "target_area", "delivery_date", "period"]
    )
    simout = []
    multout = []
    lp_count = 0
    for case, removed_names in release.items():
        print("release", case, flush=True)
        for (day, period), ng in ngroups.items():
            order = [(a, day, period) for a in AREAS]
            g = rg.loc[order]
            net = pg.loc[order].net_demand_B_mw.to_numpy(float)
            u = g.upper_limited_mw.to_numpy(float)
            coeff = np.array([json.loads(x) for x in ng.coefficients_json], float)
            lo = ng.lower_mw.to_numpy(float)
            hi = ng.upper_mw.to_numpy(float)
            c, selected_lo, h = select_constraints(names, coeff, lo, hi, removed_names)
            reuse = case in ["saved_all", "release_all"]
            ns = "saved_plan_constraints" if case == "saved_all" else "no_transmission_bounds"
            if reuse:
                fullstatus = fullold.loc[(ns, FOCUS[0], day, period), "all_firms_status"]
            else:
                fullstatus = simultaneous_feasibility(net, u, inc, c, selected_lo, h)["status"]
                lp_count += 1
            for code in FOCUS:
                up = (g.upper_limited_mw - g["remove_" + code + "_mw"]).to_numpy(float)
                if reuse:
                    afterstatus = fullold.loc[
                        (ns, code, day, period), "removed_simultaneous_status"
                    ]
                else:
                    afterstatus = simultaneous_feasibility(net, up, inc, c, selected_lo, h)[
                        "status"
                    ]
                    lp_count += 1
                simout.append(
                    dict(
                        release_case=case,
                        delivery_date=day,
                        period=int(period),
                        entity_code=code,
                        all_firms_status=fullstatus,
                        removed_simultaneous_status=afterstatus,
                    )
                )
                for area, entity in TARGETS:
                    if entity != code:
                        continue
                    target = AREAS.index(area)
                    if reuse:
                        row = rold.loc[(ns, area, day, period)]
                        status = row.status
                        value = row.lambda_removed
                    elif net[target] <= 0:
                        status = "nonpositive_target_net"
                        value = None
                    elif fullstatus == "infeasible":
                        status = "all_firms_infeasible"
                        value = None
                    elif fullstatus != "feasible":
                        status = "numerical_failure"
                        value = None
                    else:
                        a = maximum_multiplier(net, up, target, inc, c, selected_lo, h)
                        lp_count += 1
                        status = a["status"]
                        value = a["multiplier"]
                        if status == "other_regions_infeasible":
                            status = "removed_other_regions_infeasible"
                    multout.append(
                        dict(
                            release_case=case,
                            delivery_date=day,
                            period=int(period),
                            target_area=area,
                            entity_code=code,
                            status=status,
                            lambda_removed=value,
                        )
                    )
    release_sim = pd.DataFrame(simout)
    release_mult = pd.DataFrame(multout)
    assert len(release_sim) == 14 * 4 * 1440 and len(release_mult) == 14 * 5 * 1440
    write(release_sim, "constraint_release_simultaneous_all_times")
    write(release_mult, "constraint_release_multipliers_all_times")
    transitions = []
    mrows = []
    srows = []
    sk = ["delivery_date", "period", "entity_code"]
    mk = sk + ["target_area"]
    b = release_sim[release_sim.release_case.eq("saved_all")]
    bm = release_mult[release_mult.release_case.eq("saved_all")]
    for case in release:
        after = release_sim[release_sim.release_case.eq(case)]
        pair = b.merge(
            after,
            on=sk,
            validate="one_to_one",
            how="outer",
            suffixes=("_before", "_after"),
            indicator=True,
        )
        assert pair._merge.eq("both").all()
        for code, g in pair.groupby("entity_code"):
            assert not (
                g.removed_simultaneous_status_before.eq("feasible")
                & g.removed_simultaneous_status_after.eq("infeasible")
            ).any()
            srows.append(
                dict(
                    release_case=case,
                    entity_code=code,
                    time_keys=len(g),
                    base_all_firms_feasible=int(g.all_firms_status_before.eq("feasible").sum()),
                    after_all_firms_feasible=int(g.all_firms_status_after.eq("feasible").sum()),
                    removed_feasible_before=int(
                        g.removed_simultaneous_status_before.eq("feasible").sum()
                    ),
                    removed_feasible_after=int(
                        g.removed_simultaneous_status_after.eq("feasible").sum()
                    ),
                    removed_failure_to_feasible=int(
                        (
                            g.removed_simultaneous_status_before.eq("infeasible")
                            & g.removed_simultaneous_status_after.eq("feasible")
                        ).sum()
                    ),
                    recovered_where_baseline_already_feasible=int(
                        (
                            g.all_firms_status_before.eq("feasible")
                            & g.removed_simultaneous_status_before.eq("infeasible")
                            & g.removed_simultaneous_status_after.eq("feasible")
                        ).sum()
                    ),
                )
            )
        mp = bm.merge(
            release_mult[release_mult.release_case.eq(case)],
            on=mk,
            validate="one_to_one",
            how="outer",
            suffixes=("_before", "_after"),
            indicator=True,
        )
        assert mp._merge.eq("both").all()
        for area, g in mp.groupby("target_area"):
            common = g.status_before.eq("valid") & g.status_after.eq("valid")
            diff = g.loc[common, "lambda_removed_after"] - g.loc[common, "lambda_removed_before"]
            assert (diff >= -1e-7).all()
            mrows.append(
                dict(
                    release_case=case,
                    target_area=area,
                    time_keys=len(g),
                    common_valid=int(common.sum()),
                    newly_valid=int(
                        (~g.status_before.eq("valid") & g.status_after.eq("valid")).sum()
                    ),
                    lost_valid=int(
                        (g.status_before.eq("valid") & ~g.status_after.eq("valid")).sum()
                    ),
                    lambda_difference_median=diff.median(),
                    lambda_difference_max=diff.max(),
                    changed_common=int(diff.gt(1e-7).sum()),
                    median_before_common=g.loc[common, "lambda_removed_before"].median(),
                    median_after_common=g.loc[common, "lambda_removed_after"].median(),
                )
            )
        tr = (
            mp.groupby(["target_area", "status_before", "status_after"])
            .size()
            .rename("time_keys")
            .reset_index()
        )
        tr["release_case"] = case
        transitions.append(tr)
    pd.DataFrame(srows).to_csv(qa / "release_simultaneous_summary.csv", index=False)
    pd.DataFrame(mrows).to_csv(qa / "release_multiplier_summary.csv", index=False)
    pd.concat(transitions).to_csv(qa / "release_multiplier_status_transitions.csv", index=False)
    # 供給範囲：元観測がある非固定ラベルだけ。観測なしは別の件数として残す。
    assets = read(root / SOURCE / "asset_attribution_proxy.parquet")
    scope = []
    raw_capacity = pd.to_numeric(
        assets["diagnostic_mapping__capacity_reference_raw_kw"], errors="coerce"
    )
    rawmap = dict(zip(assets.asset_key_candidate, raw_capacity, strict=True))
    rolemap = dict(zip(assets.asset_key_candidate, assets.candidate_analysis_role, strict=True))
    raw_invalid = int(
        (
            assets["diagnostic_mapping__capacity_reference_raw_kw"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            & raw_capacity.isna()
        ).sum()
    )
    for file in sorted((root / SOURCE / "unit_trial_uppers").glob("*.parquet")):
        print("scope", file.stem, flush=True)
        x = read(file)
        x = x[x.area.isin(AREAS)].copy()
        x["role"] = x.asset_key_candidate.map(rolemap)
        assert x.role.notna().all()
        x = x[x.role.ne("fixed_observed_nuclear_candidate")].copy()
        # 原子力はrole名だけを信用せず対象asset一覧と交差点検。
        assert not x.asset_key_candidate.isin(
            assets.loc[
                assets.candidate_analysis_role.eq("fixed_observed_nuclear_candidate"),
                "asset_key_candidate",
            ]
        ).any()
        x["outside_count"] = (~x.trial_upper_defined).astype(int)
        x["outside_known_reference_mw"] = [
            float(c) / 1000 if (not ok and pd.notna(c)) else np.nan
            for ok, c in zip(x.trial_upper_defined, x.capacity_reference_kw, strict=True)
        ]
        x["outside_reference_unknown_count"] = (
            ~x.trial_upper_defined & x.capacity_reference_kw.isna()
        ).astype(int)
        x["raw_capacity_kw"] = x.asset_key_candidate.map(rawmap)
        x["outside_known_raw_snapshot_mw"] = (x.raw_capacity_kw / 1000).where(
            ~x.trial_upper_defined
        )
        x["outside_raw_snapshot_unknown_count"] = (
            ~x.trial_upper_defined & x.raw_capacity_kw.isna()
        ).astype(int)
        x["record_held_count"] = (
            x.eligible_adjustable_candidate
            & x.scenario_status.isin(RECORD_HELD)
            & ~x.trial_upper_defined
        ).astype(int)
        a = (
            x.groupby(KEY)
            .agg(
                observed_nonfixed_labels=("asset_key_candidate", "size"),
                outside_calculation_count=("outside_count", "sum"),
                outside_reference_unknown_count=("outside_reference_unknown_count", "sum"),
                outside_raw_snapshot_unknown_count=("outside_raw_snapshot_unknown_count", "sum"),
                record_held_count=("record_held_count", "sum"),
                outside_known_reference_mw=(
                    "outside_known_reference_mw",
                    lambda z: z.sum(min_count=1),
                ),
                outside_known_raw_snapshot_mw=(
                    "outside_known_raw_snapshot_mw",
                    lambda z: z.sum(min_count=1),
                ),
            )
            .reset_index()
        )
        a["supply_trial_scenario"] = file.stem
        a["unit_source_file"] = str(file.relative_to(root))
        scope.append(a)
    scopes = pd.concat(scope, ignore_index=True)
    assert len(scopes) == 220320
    merged = regional.merge(
        scopes,
        on=["supply_trial_scenario"] + KEY,
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    assert merged._merge.eq("both").all()
    merged = merged.drop(columns="_merge")
    # 全registry設備数と観測ラベル数を別々に表示。未観測unit日を供給入力へ生成しない。
    role_nuclear = assets.candidate_analysis_role.eq("fixed_observed_nuclear_candidate")
    counts = assets[assets.area.isin(AREAS) & ~role_nuclear].groupby("area").size()
    merged["registry_nonfixed_labels"] = merged.area.map(counts)
    merged["registry_labels_without_observation"] = (
        merged.registry_nonfixed_labels - merged.observed_nonfixed_labels
    )
    assert merged.registry_labels_without_observation.ge(0).all()
    ledger = []
    for code in FOCUS:
        g = merged.copy()
        g["entity_code"] = code
        g["removed_target_upper_mw"] = g["remove_" + code + "_mw"]
        g["remaining_upper_mw"] = g.upper_limited_mw - g.removed_target_upper_mw
        original = g[g.supply_trial_scenario.str.endswith("__record_held_reference")].copy()
        original["original_scenario"] = original.supply_trial_scenario.str.removesuffix(
            "__record_held_reference"
        )
        base = g[~g.supply_trial_scenario.str.endswith("__record_held_reference")][
            ["supply_trial_scenario"] + KEY + ["upper_limited_mw", "removed_target_upper_mw"]
        ].rename(
            columns={
                "supply_trial_scenario": "original_scenario",
                "upper_limited_mw": "before_upper",
                "removed_target_upper_mw": "before_removal",
            }
        )
        restore = original.merge(
            base, on=["original_scenario"] + KEY, validate="one_to_one", how="left", indicator=True
        )
        assert restore._merge.eq("both").all()
        restore["other_reference_return_mw"] = (restore.upper_limited_mw - restore.before_upper) - (
            restore.removed_target_upper_mw - restore.before_removal
        )
        returns = restore[["original_scenario"] + KEY + ["other_reference_return_mw"]].rename(
            columns={"original_scenario": "supply_trial_scenario"}
        )
        # 元条件側へ、対となる比較条件で他者分が戻る量を併記。
        # no-deductionには適用対象なしnull。
        g = g.merge(
            returns,
            on=["supply_trial_scenario"] + KEY,
            how="left",
            validate="one_to_one",
            indicator="return_pair_join",
        )
        ledger.append(g)
    ledger = pd.concat(ledger, ignore_index=True)
    assert len(ledger) == 4 * 220320
    keep = (
        ["supply_trial_scenario"]
        + KEY
        + [
            "entity_code",
            "upper_limited_mw",
            "removed_target_upper_mw",
            "remaining_upper_mw",
            "other_reference_return_mw",
            "return_pair_join",
            "observed_nonfixed_labels",
            "registry_nonfixed_labels",
            "registry_labels_without_observation",
            "defined_candidate_count",
            "held_candidate_count",
            "outside_calculation_count",
            "outside_reference_unknown_count",
            "outside_raw_snapshot_unknown_count",
            "record_held_count",
            "outside_known_reference_mw",
            "outside_known_raw_snapshot_mw",
            "unit_source_file",
        ]
    )
    write(ledger[keep], "focus_regional_supply_scope_all_times")
    scope_summary = (
        merged.groupby(["supply_trial_scenario", "area"])
        .agg(
            time_keys=("period", "size"),
            outside_count_min=("outside_calculation_count", "min"),
            outside_count_max=("outside_calculation_count", "max"),
            unknown_raw_capacity_labels_max=("outside_raw_snapshot_unknown_count", "max"),
            known_outside_raw_snapshot_mw_max=("outside_known_raw_snapshot_mw", "max"),
            record_held_count_max=("record_held_count", "max"),
        )
        .reset_index()
    )
    scope_summary.to_csv(qa / "supply_scope_summary.csv", index=False)
    decomposition = decompose_supply_scope(ledger[keep])
    write(decomposition, "supply_scope_decomposition_all_times")
    totals = decomposition.groupby(
        ["supply_trial_scenario", "entity_code", "delivery_date", "period"]
    )[
        [
            "known_numeric_scenario_reduction_other_mw",
            "other_reference_return_mw",
            "total_reduction_other_mw",
        ]
    ].sum()
    totals.groupby(["supply_trial_scenario", "entity_code"]).agg(["min", "mean", "max"]).to_csv(
        qa / "supply_decomposition_summary.csv"
    )
    # 代表：17条件で全法人成立・除去後不成立の最初。
    # なければrefで全法人成立・除去後不成立の最初。
    representative = []
    regional_examples = []
    constraint_examples = []
    cref = consistent[consistent.network_scenario.eq("saved_plan_constraints")]
    reference_sim = sim[
        sim.supply_trial_scenario.eq(REF) & sim.network_scenario.eq("saved_plan_constraints")
    ]
    regall = regional.set_index(["supply_trial_scenario"] + KEY)
    for code in FOCUS:
        g = cref[
            cref.entity_code.eq(code)
            & cref.consistency_state.eq("all17_base_feasible_removed_infeasible")
        ].sort_values(["delivery_date", "period"])
        rule = "17条件全て全法人成立かつ除去後不成立の最初の時刻"
        if g.empty:
            g = reference_sim[
                reference_sim.entity_code.eq(code)
                & reference_sim.all_firms_status.eq("feasible")
                & reference_sim.removed_simultaneous_status.eq("infeasible")
            ].sort_values(["delivery_date", "period"])
            rule = "上記なしのため停止控除なしで全法人成立かつ除去後不成立の最初の時刻"
        if g.empty:
            continue
        row = g.iloc[0]
        day, period = row.delivery_date, int(row.period)
        order = [(a, day, period) for a in AREAS]
        rg0 = regall.loc[[(REF, a, day, period) for a in AREAS]]
        pp = pg.loc[order]
        n = pp.net_demand_B_mw.to_numpy(float)
        up = (rg0.upper_limited_mw - rg0["remove_" + code + "_mw"]).to_numpy(float)
        ng = ngroups[day, period]
        c = np.array([json.loads(v) for v in ng.coefficients_json], float)
        lo = ng.lower_mw.to_numpy(float)
        hi = ng.upper_mw.to_numpy(float)
        diag = diagnostic_slack(n, up, inc, c, lo, hi)
        representative.append(
            dict(
                entity_code=code,
                delivery_date=day,
                period=period,
                selection_rule=rule,
                price_used=False,
                supply_trial_scenario=REF,
                diagnostic_slack_sum_mw=diag["diagnostic_slack_sum_mw"],
                diagnostic_residual_mw=diag["diagnostic_residual_mw"],
            )
        )
        for i, a in enumerate(AREAS):
            regional_examples.append(
                dict(
                    entity_code=code,
                    delivery_date=day,
                    period=period,
                    area=a,
                    demand_actual_mw=float(pp.demand_actual_sample_mw.iloc[i]),
                    fixed_actual_R_mw=float(pp.fixed_actual_mw.iloc[i]),
                    net_demand_mw=n[i],
                    all_firms_known_upper_mw=float(rg0.upper_limited_mw.iloc[i]),
                    removed_target_upper_mw=float(rg0["remove_" + code + "_mw"].iloc[i]),
                    remaining_upper_mw=up[i],
                    diagnostic_deficit_mw=diag["diagnostic_deficit_mw"][i],
                    diagnostic_surplus_mw=diag["diagnostic_surplus_mw"][i],
                )
            )
        for j, name in enumerate(names):
            constraint_examples.append(
                dict(
                    entity_code=code,
                    delivery_date=day,
                    period=period,
                    constraint_name=name,
                    coefficients_json=ng.coefficients_json.iloc[j],
                    lower_mw=lo[j],
                    upper_mw=hi[j],
                )
            )
    pd.DataFrame(representative).to_csv(qa / "representative_selection.csv", index=False)
    pd.DataFrame(regional_examples).to_csv(qa / "representative_regional_account.csv", index=False)
    pd.DataFrame(constraint_examples).to_csv(qa / "representative_constraints.csv", index=False)
    join_stats = (
        ledger.groupby(["supply_trial_scenario", "return_pair_join"])
        .size()
        .rename("rows")
        .reset_index()
    )
    join_stats["applicable"] = ~join_stats.supply_trial_scenario.eq(
        REF
    ) & ~join_stats.supply_trial_scenario.str.endswith("__record_held_reference")
    assert join_stats.loc[join_stats.applicable, "return_pair_join"].eq("both").all()
    assert join_stats.loc[~join_stats.applicable, "return_pair_join"].eq("left_only").all()
    join_stats.to_csv(qa / "return_pair_join_statistics.csv", index=False)
    representative_boundary_checks(
        pd.DataFrame(regional_examples), pd.DataFrame(constraint_examples)
    ).to_csv(qa / "representative_cut_checks.csv", index=False)
    # 参照期間・元セル/固定入力の不変性。
    for path, h in inputs.items():
        assert sha(root / path) == h, path
    audit = {
        "version": output.name,
        "inputs": [dict(path=p, sha256=h) for p, h in inputs.items()],
        "outputs": outputs,
        "all17_condition_time_rows": len(consistent),
        "stop_pair_rows": len(pairs),
        "release_cases": len(release),
        "release_simultaneous_rows": len(release_sim),
        "release_multiplier_rows": len(release_mult),
        "regional_supply_scope_rows": len(ledger),
        "new_lp_calls": lp_count,
        "joins_match_rate": 1.0,
        "return_pair_applicable_match_rate": 1.0,
        "return_pair_all_rows_match_rate": float(ledger.return_pair_join.eq("both").mean()),
        "return_pair_not_applicable_rows": int(ledger.return_pair_join.eq("left_only").sum()),
        "unmatched": 0,
        "silent_drop": 0,
        "raw_snapshot_capacity_non_numeric_count": raw_invalid,
        "fixed_R_removed": False,
        "prices_used_for_selection": False,
        "formal_rsi_or_final_baseline": False,
    }
    (output / "manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    (qa / "run_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print("complete", len(release_mult), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    run(Path(__file__).resolve().parents[1], a.output.resolve(), a.report.resolve())
