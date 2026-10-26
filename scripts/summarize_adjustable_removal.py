"""倍率・全法人比較・停止条件の共通時刻集計を再生成する。"""

import argparse
import json
from pathlib import Path

import pandas as pd


def run(root, folder):
    qa = root / "reports/21_adjustable_removal/qa"
    r = pd.read_parquet(folder / "all_and_removed_net_multipliers.parquet")
    valid = r[r.status.eq("valid")]
    keys = ["supply_trial_scenario", "target_area", "entity", "network_scenario"]
    rows = []
    for k, g in r.groupby(keys):
        v = g[g.status.eq("valid")]
        rows.append(
            dict(zip(keys, k, strict=True))
            | {
                "total_time_keys": len(g),
                "common_all_and_removed_valid": len(v),
                "lambda_all_median_common": v.lambda_all_firms.median(),
                "lambda_removed_median_common": v.lambda_removed.median(),
                "lambda_removed_lt1_common": int((v.lambda_removed < 1 - 1e-8).sum()),
                "remaining_status_counts_json": json.dumps(
                    g.loc[~g.status.eq("valid"), "status"].value_counts().to_dict(),
                    ensure_ascii=False,
                ),
            }
        )
    pd.DataFrame(rows).to_csv(qa / "matched_all_vs_removed_summary.csv", index=False)
    base = r[r.supply_trial_scenario.eq("reference_no_deduction")]
    comparison = []
    for name, g in r.groupby("supply_trial_scenario"):
        if name == "reference_no_deduction":
            continue
        paired = g.merge(
            base,
            on=["delivery_date", "period", "target_area", "entity", "network_scenario"],
            how="outer",
            validate="one_to_one",
            suffixes=("", "_reference"),
            indicator=True,
        )
        assert paired._merge.eq("both").all()
        for k, f in paired.groupby(["target_area", "entity", "network_scenario"]):
            common = f[f.status.eq("valid") & f.status_reference.eq("valid")]
            comparison.append(
                {
                    "scenario": name,
                    "target_area": k[0],
                    "entity": k[1],
                    "network_scenario": k[2],
                    "common_valid": len(common),
                    "lt1_current_common": int((common.lambda_removed < 1 - 1e-8).sum()),
                    "lt1_reference_common": int((common.lambda_removed_reference < 1 - 1e-8).sum()),
                    "median_lambda_current_common": common.lambda_removed.median(),
                    "median_lambda_reference_common": common.lambda_removed_reference.median(),
                }
            )
    pd.DataFrame(comparison).to_csv(qa / "stop_scenario_common_time_comparison.csv", index=False)
    restored = []
    for name, g in r[r.supply_trial_scenario.str.endswith("__record_held_reference")].groupby(
        "supply_trial_scenario"
    ):
        old = r[r.supply_trial_scenario.eq(name.removesuffix("__record_held_reference"))]
        p = g.merge(
            old,
            on=["delivery_date", "period", "target_area", "entity", "network_scenario"],
            how="outer",
            validate="one_to_one",
            suffixes=("", "_old"),
            indicator=True,
        )
        assert p._merge.eq("both").all()
        v = p[p.status.eq("valid") & p.status_old.eq("valid")]
        assert (v.lambda_removed >= v.lambda_removed_old - 1e-7).all()
        restored.append(
            {
                "scenario": name,
                "common_valid": len(v),
                "minimum_lambda_change_common": (v.lambda_removed - v.lambda_removed_old).min(),
                "maximum_lambda_change_common": (v.lambda_removed - v.lambda_removed_old).max(),
            }
        )
    pd.DataFrame(restored).to_csv(qa / "record_fallback_monotonicity.csv", index=False)
    first = (
        valid[
            valid.network_scenario.eq("saved_plan_constraints")
            & valid.supply_trial_scenario.eq("reference_no_deduction")
        ]
        .sort_values(["delivery_date", "period"])
        .groupby("target_area")
        .head(1)
    )
    first.to_csv(qa / "first_valid_examples.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.output.resolve())
