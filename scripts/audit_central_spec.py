"""工程25：2設備の別版比較、実ソルバー入力と既存全件の照合。"""

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
from src.adjustable_removal import maximum_multiplier, trial_upper  # noqa: E402
from src.april_constraint_diagnosis import condition_consistency  # noqa: E402
from src.april_trial_comparison import simultaneous_feasibility  # noqa: E402
from src.central_spec_audit import (  # noqa: E402
    apply_two_asset_extension,
    capture_solver_call,
    minimum_feasible_multiplier,
)
from src.net_demand_network import AREAS, EDGES  # noqa: E402

OLD = Path("data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3")
SIM = Path("data/interim/occto/april_trial_comparison/20261027_comparison_v1")
IDS = (
    "182a25b5db172b0c99d48004bd40c1b57bf12e5e0cb918181cd8d5fbb5a0bbbe",
    "1f70aac24549c68ac6712af93662e5e0d2df247faac3cb3e09f0b8c1f1b12e88",
)
TIME = ["delivery_date", "period"]
SC = "supply_trial_scenario"
NS = "network_scenario"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(root, output, report):
    if output.exists() or (report / "qa/run_audit.json").exists():
        raise ValueError("固定版を上書きしない")
    output.mkdir(parents=True)
    qa = report / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    inputs = {}
    outputs = []

    def read(path, **kwargs):
        inputs[str(path.relative_to(root))] = sha(path)
        return pq.read_table(path, **kwargs).to_pandas()

    def write(df, name):
        path = output / (name + ".parquet")
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, path, compression="zstd")
        assert pq.read_table(path).equals(table)
        outputs.append(dict(path=str(path.relative_to(root)), sha256=sha(path), rows=len(df)))

    assets = read(root / OLD / "asset_attribution_proxy.parquet")
    two = assets[assets.asset_key_candidate.isin(IDS)].copy()
    assert len(two) == 2 and two["発電方式・燃種"].fillna("").eq("").all()
    assert (~two.eligible_adjustable_candidate).all()
    two["external_pv_wind_nonoverlap_supported"] = True
    two["conditional_adjustable_use"] = True
    two["official_mapping_status_changed"] = False
    two["measurement_boundary_verified"] = False
    two["external_evidence_scope"] = (
        "100MW wholesale reference slice;112MW generator-end reference; not net market supply"
    )
    write(two, "two_asset_external_evidence_extension")
    regional = read(root / OLD / "regional_trial_uppers.parquet")
    oldsim = read(root / SIM / "nine_area_simultaneous_after_removal.parquet")
    oldmult = read(root / OLD / "all_and_removed_net_multipliers.parquet")
    oldlink = oldmult.merge(
        oldsim[[SC, NS, *TIME, "entity_code", "removed_simultaneous_status"]],
        on=[SC, NS, *TIME, "entity_code"],
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    assert len(oldlink) == 440640 and oldlink._merge.eq("both").all()
    valid = oldlink.status.eq("valid")
    mismatches = oldlink[
        valid
        & (
            (
                oldlink.lambda_removed.ge(1 + 1e-8)
                & oldlink.removed_simultaneous_status.ne("feasible")
            )
            | (
                oldlink.lambda_removed.lt(1 - 1e-8)
                & oldlink.removed_simultaneous_status.eq("feasible")
            )
        )
    ]
    mismatches.to_csv(qa / "old_lambda_one_exceptions.csv", index=False)
    assert len(mismatches) == 0
    primary = read(
        root
        / "data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"
        / "B30_primary_input_ledger.parquet"
    )
    assert len(primary) == 12960 and not primary.duplicated(["area", *TIME]).any()
    primary = primary.set_index([*TIME, "area"])
    network = read(
        root
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet",
        filters=[
            ("delivery_date", ">=", date(2025, 4, 1)),
            ("delivery_date", "<=", date(2025, 4, 30)),
        ],
    )
    nets = {}
    for t, g in network.groupby(TIME):
        g = g.sort_values("constraint_name")
        nets[t] = (
            np.array([json.loads(x) for x in g.coefficients_json], float),
            g.lower_mw.to_numpy(float),
            g.upper_mw.to_numpy(float),
            g,
        )
    incidence = np.zeros((9, 10))
    for k, (_, s, r) in enumerate(EDGES):
        incidence[AREAS.index(s), k] = -1
        incidence[AREAS.index(r), k] = 1
    upperlookup = regional.set_index([SC, *TIME, "area"]).sort_index()
    simlookup = oldsim.set_index([SC, NS, *TIME, "entity_code"])
    ml = oldmult.set_index([SC, NS, *TIME, "target_area"])
    rows = []
    simrows = []
    regrows = []
    audits = []
    cache = {}
    lp_calls = 0

    def check(n, u, c, lo, hi, constrained):
        nonlocal lp_calls
        key = (n.tobytes(), u.tobytes(), c.tobytes(), lo.tobytes(), hi.tobytes(), constrained)
        if key not in cache:
            result = simultaneous_feasibility(n, u, incidence, c, lo, hi, constrained=constrained)
            if constrained:
                lp_calls += 1
            cache[key] = result["status"]
        return cache[key]

    for case in sorted(regional[SC].unique()):
        print("case", case, flush=True)
        cells = read(
            root / OLD / "unit_trial_uppers" / (case + ".parquet"),
            filters=[("asset_key_candidate", "in", list(IDS))],
        )
        assert len(cells) == 2880 and not cells.duplicated(["asset_key_candidate", *TIME]).any()
        expected_schema = pq.read_schema(root / OLD / "unit_trial_uppers" / (case + ".parquet"))
        assert cells.columns.tolist() == expected_schema.names
        added = []
        rest = []
        for r in cells.itertuples(index=False):
            v, b = trial_upper(
                r.adjusted_reference_upper_kw,
                r.capacity_reference_kw,
                r.scenario_status,
                restore_record_held=case.endswith("__record_held_reference"),
            )
            added.append(v if v is not None and v >= 0 else None)
            rest.append(b)
        # 元列を全て保持し、元のtrial nullも上書きしない。
        cells["extension_adjustable_upper_kw"] = added
        cells["extension_record_held_reference_restored"] = rest
        cells["extension_nonoverlap_supported"] = True
        cells["extension_boundary_verified"] = False
        cells["extension_reference_scope_assumed"] = True
        # ケース固有の公表記録列と宣言型をそのまま保存。条件間concatで型推測しない。
        source_table = pq.read_table(
            root / OLD / "unit_trial_uppers" / (case + ".parquet"),
            filters=[("asset_key_candidate", "in", list(IDS))],
        )
        typed = source_table
        for column in cells.columns[len(source_table.column_names) :]:
            typed = typed.append_column(column, pa.array(cells[column]))
        typed_path = output / "unit_extensions" / (case + ".parquet")
        typed_path.parent.mkdir(exist_ok=True)
        pq.write_table(typed, typed_path, compression="zstd")
        reread = pq.read_table(typed_path)
        assert reread.equals(typed)
        assert reread.select(source_table.column_names).equals(source_table)
        outputs.append(
            dict(path=str(typed_path.relative_to(root)), sha256=sha(typed_path), rows=len(cells))
        )
        cellgroups = {t: g for t, g in cells.groupby(TIME)}
        for (d, p), g in regional[regional[SC].eq(case)].groupby(TIME, sort=True):
            g = g.set_index("area").loc[list(AREAS)]
            values = cellgroups[d, p].extension_adjustable_upper_kw.tolist()
            ext, inc, defined, held = apply_two_asset_extension(g.upper_limited_mw.iloc[0], values)
            assert ext - g.upper_limited_mw.iloc[0] == inc
            u = g.upper_limited_mw.to_numpy(float)
            u[0] += float(inc)
            n = primary.loc[[(d, p, a) for a in AREAS]].net_demand_B_mw.to_numpy(float)
            c, lo, hi, ng = nets[d, p]
            regrows.append(
                {
                    SC: case,
                    "delivery_date": d,
                    "period": int(p),
                    "added_hokkaido_upper_mw": inc,
                    "extension_defined_assets": defined,
                    "extension_held_assets": held,
                    "original_hokkaido_upper_mw": g.upper_limited_mw.iloc[0],
                    "extended_hokkaido_upper_mw": g.upper_limited_mw.iloc[0] + inc,
                    "hokkaido_removed_mw_unchanged": g.remove_hokkaido_mw.iloc[0],
                }
            )
            for constrained, ns in [
                (True, "saved_plan_constraints"),
                (False, "no_transmission_bounds"),
            ]:
                oldbase = simlookup.loc[(case, ns, d, p, "hokkaido")].all_firms_status
                base = oldbase if oldbase == "feasible" else check(n, u, c, lo, hi, constrained)
                for code in ENTITIES:
                    old = simlookup.loc[(case, ns, d, p, code)]
                    rem = u - g["remove_" + code + "_mw"].to_numpy(float)
                    # 非負上限だけの追加は成立解を失わせない。旧成立は証明済み解を継承。
                    result = old.removed_simultaneous_status
                    method = "old_feasible_monotone_upper_extension"
                    if result != "feasible":
                        result = check(n, rem, c, lo, hi, constrained)
                        method = "same_LP_or_identical_input_cache"
                    simrows.append(
                        {
                            SC: case,
                            NS: ns,
                            "delivery_date": d,
                            "period": int(p),
                            "entity_code": code,
                            "all_firms_status": base,
                            "removed_simultaneous_status": result,
                            "old_all_firms_status": old.all_firms_status,
                            "old_removed_simultaneous_status": old.removed_simultaneous_status,
                            "verification_method": method,
                            "added_other_supply_mw": float(inc),
                        }
                    )
                old = ml.loc[(case, ns, d, p, "北海道")]
                rem = u - g.remove_hokkaido_mw.to_numpy(float)
                if n[0] <= 0:
                    status = "nonpositive_target_net"
                    r = {}
                    a = {}
                elif base == "infeasible":
                    status = "all_firms_infeasible"
                    r = {}
                    a = {}
                elif base != "feasible":
                    status = "numerical_failure"
                    r = {}
                    a = {}
                else:
                    r = maximum_multiplier(n, rem, 0, incidence, c, lo, hi, constrained=constrained)
                    a = maximum_multiplier(n, u, 0, incidence, c, lo, hi, constrained=constrained)
                    status = (
                        "valid" if r["status"] == "valid" else "removed_other_regions_infeasible"
                    )
                    if r["status"] not in ["valid", "other_regions_infeasible"]:
                        status = r["status"]
                rows.append(
                    {
                        SC: case,
                        NS: ns,
                        "delivery_date": d,
                        "period": int(p),
                        "target_area": "北海道",
                        "entity_code": "hokkaido",
                        "status": status,
                        "old_status": old.status,
                        "lambda_removed": r.get("multiplier"),
                        "old_lambda_removed": old.lambda_removed,
                        "lambda_all_firms": a.get("multiplier"),
                        "added_other_supply_mw": float(inc),
                    }
                )
        # 実呼出しから行列を捕捉。価格に依存せず4/1 period1、控除なしを選定。
        if case == "reference_no_deduction":
            d = date(2025, 4, 1)
            p = 1
            g = upperlookup.loc[(case, d, p)].loc[list(AREAS)]
            n = primary.loc[[(d, p, a) for a in AREAS]].net_demand_B_mw.to_numpy(float)
            c, lo, hi, ng = nets[d, p]
            for target, code in [("北海道", "hokkaido"), ("東京", "jera"), ("中部", "jera")]:
                for extension in [False, True]:
                    u = (g.upper_limited_mw - g["remove_" + code + "_mw"]).to_numpy(float)
                    if extension:
                        u[0] += 212
                    result, call = capture_solver_call(
                        maximum_multiplier, n, u, AREAS.index(target), incidence, c, lo, hi
                    )
                    sim, scall = capture_solver_call(
                        simultaneous_feasibility, n, u, incidence, c, lo, hi
                    )
                    # 全法人時の固定需要行列も同じサンプルに保存。
                    au = g.upper_limited_mw.to_numpy(float)
                    if extension:
                        au[0] += 212
                    full, fcall = capture_solver_call(
                        simultaneous_feasibility, n, au, incidence, c, lo, hi
                    )
                    audits.append(
                        dict(
                            target_area=target,
                            entity_code=code,
                            extension=extension,
                            delivery_date=str(d),
                            period=p,
                            areas=list(AREAS),
                            edges=EDGES,
                            demand_actual_mw=primary.loc[
                                [(d, p, a) for a in AREAS]
                            ].demand_actual_sample_mw.to_list(),
                            fixed_R_mw=primary.loc[
                                [(d, p, a) for a in AREAS]
                            ].fixed_actual_mw.to_list(),
                            net_mw=n.tolist(),
                            remaining_upper_mw=u.tolist(),
                            raw_network_constraints=ng.to_dict("records"),
                            multiplier_result=result,
                            multiplier_call=call,
                            minimum_lambda=minimum_feasible_multiplier(call),
                            simultaneous_result=sim,
                            simultaneous_call=scall,
                            all_firms_simultaneous_result=full,
                            all_firms_simultaneous_call=fcall,
                            original_regional_input=g.reset_index().to_dict("records"),
                        )
                    )
    extended = pd.DataFrame(rows)
    sim = pd.DataFrame(simrows)
    assert len(extended) == 48960 and len(sim) == 391680
    assert not extended.duplicated([SC, NS, *TIME]).any()
    assert not sim.duplicated([SC, NS, *TIME, "entity_code"]).any()
    write(pd.DataFrame(regrows), "two_asset_addition_by_condition_time")
    write(extended, "hokkaido_multiplier_extension_all_times")
    write(sim, "nine_area_simultaneous_extension_all_times")
    consistency = []
    for k, g in sim.groupby([NS, "entity_code", *TIME]):
        consistency.append(dict(zip([NS, "entity_code", *TIME], k)) | condition_consistency(g))
    cons = pd.DataFrame(consistency)
    write(cons, "condition_consistency_extension_all_times")
    extended.groupby([SC, NS, "status"]).size().rename("time_keys").reset_index().to_csv(
        qa / "hokkaido_status_counts.csv", index=False
    )
    summaries = []
    for (case, ns), g in extended.groupby([SC, NS]):
        valid = g[g.status.eq("valid")]
        common = g[g.status.eq("valid") & g.old_status.eq("valid")]
        summaries.append(
            {
                SC: case,
                NS: ns,
                "all_times": len(g),
                "valid": len(valid),
                "below_one": int(valid.lambda_removed.lt(1 - 1e-8).sum()),
                "old_valid": int(g.old_status.eq("valid").sum()),
                "old_below_one": int(
                    (g.old_status.eq("valid") & g.old_lambda_removed.lt(1 - 1e-8)).sum()
                ),
                "common_valid": len(common),
                "old_median_common": common.old_lambda_removed.median(),
                "new_median_common": common.lambda_removed.median(),
                "min_change_common": (common.lambda_removed - common.old_lambda_removed).min(),
                "max_change_common": (common.lambda_removed - common.old_lambda_removed).max(),
            }
        )
    pd.DataFrame(summaries).to_csv(qa / "hokkaido_multiplier_comparison.csv", index=False)
    sim.groupby(
        [
            SC,
            NS,
            "entity_code",
            "old_all_firms_status",
            "all_firms_status",
            "old_removed_simultaneous_status",
            "removed_simultaneous_status",
        ]
    ).size().rename("time_keys").reset_index().to_csv(
        qa / "simultaneous_transitions.csv", index=False
    )
    cons.groupby([NS, "entity_code", "consistency_state"]).size().rename(
        "time_keys"
    ).reset_index().to_csv(qa / "consistency_counts.csv", index=False)
    oldcons = read(
        root
        / "data/interim/occto/april_constraint_diagnosis/20261027_constraints_v2"
        / "condition_consistency_all_times.parquet"
    )
    cp = oldcons.merge(
        cons,
        on=[NS, "entity_code", *TIME],
        suffixes=("_old", "_new"),
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    assert cp._merge.eq("both").all() and len(cp) == 23040
    cp.groupby([NS, "entity_code", "consistency_state_old", "consistency_state_new"]).size().rename(
        "time_keys"
    ).reset_index().to_csv(qa / "consistency_transitions.csv", index=False)
    # 全件対応は経験的検査であり、数学的一般命題にしない。
    link = extended.merge(
        sim[sim.entity_code.eq("hokkaido")],
        on=[SC, NS, *TIME, "entity_code"],
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    assert len(link) == 48960 and link._merge.eq("both").all()
    mismatch = link[
        link.status.eq("valid")
        & (link.lambda_removed.ge(1 + 1e-8) != link.removed_simultaneous_status.eq("feasible"))
    ]
    assert not len(mismatch)
    (qa / "solver_calls_real_times.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    audit = dict(
        inputs=[dict(path=p, sha256=h) for p, h in inputs.items()],
        outputs=outputs,
        version=output.name,
        old_mult_frames=440640,
        old_valid_frames=int(oldlink.status.eq("valid").sum()),
        old_lambda_one_exceptions=0,
        new_hokkaido_lambda_one_exceptions=0,
        original_unit_cells_preserved=48960,
        extension_multiplier_rows=48960,
        extension_simultaneous_rows=391680,
        consistency_rows=23040,
        join_match_rate=1.0,
        unmatched=0,
        duplicates=0,
        unknown_zero_filled=False,
        original_raw_fuel_modified=False,
        fixed_R_modified=False,
        firm_removal_modified=False,
        actual_feasibility_LP_calls=lp_calls,
        feasibility_cache_size=len(cache),
        solver_audit_samples=len(audits),
        selected_sample_rule=(
            "4/1 period1、停止控除なし。北海道/JERA東京/中部、旧集合/追加集合。価格を未参照"
        ),
        final_baseline_adopted=False,
    )
    (qa / "run_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print("DONE", len(sim), lp_calls, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("reports/25_central_spec_audit"))
    a = parser.parse_args()
    run(a.data_root.resolve(), a.output.resolve(), a.report.resolve())
