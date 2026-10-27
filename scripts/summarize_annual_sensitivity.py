"""既存17条件の年間・月別対比較。新しい供給条件は作らない。"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_annual_diagnostic import OUT, ROOT

QA = ROOT / "reports/27_fy2025_annual/qa"
KEY = ["network_scenario", "target_area", "delivery_date", "period"]


def price_summary():
    path = ROOT / "data/processed/annual_diagnostic/20261027_publication_v1"
    b = pd.read_parquet(path / "explanatory_no_stop_priced.parquet")
    b = b[b.network_scenario.eq("saved_plan_constraints")].copy()
    # 表示統計用のみfloat化。原ParquetのDecimal値は変更しない。
    b["price_stat"] = b.area_price.astype(float)
    rows = []
    for area, g in b.groupby("target_area"):
        g = g.copy()
        g["price_group"] = np.where(
            g.status.ne("valid"),
            g.status,
            np.where(
                g.lambda_removed < 1 - 1e-8,
                "valid_below_one",
                np.where(g.lambda_removed > 1 + 1e-8, "valid_above_one", "numeric_boundary"),
            ),
        )
        for group, v in g.groupby("price_group"):
            rows.append(
                dict(
                    area=area,
                    group=group,
                    frames=len(v),
                    price_min=v.price_stat.min(),
                    price_q25=v.price_stat.quantile(0.25),
                    price_median=v.price_stat.median(),
                    price_q75=v.price_stat.quantile(0.75),
                    price_max=v.price_stat.max(),
                    net_demand_median=v.target_net_demand_mw.median(),
                )
            )
    pd.DataFrame(rows).to_csv(QA / "price_by_status_and_one_boundary.csv", index=False)


def main():
    rows = []
    base = []
    for month in [f"2025{m:02}" for m in range(4, 13)] + ["202601", "202602", "202603"]:
        for path in sorted(
            (OUT / "multipliers").glob(month + "__*__record_held_reference.parquet")
        ):
            before = path.with_name(path.name.replace("__record_held_reference", ""))
            a = pd.read_parquet(before)
            b = pd.read_parquet(path)
            j = a.merge(
                b,
                on=KEY,
                how="outer",
                validate="one_to_one",
                indicator=True,
                suffixes=("_before", "_restored"),
            )
            assert j._merge.eq("both").all()
            for (ns, area), g in j.groupby(["network_scenario", "target_area"]):
                common = g.status_before.eq("valid") & g.status_restored.eq("valid")
                delta = (
                    g.loc[common, "lambda_removed_restored"]
                    - g.loc[common, "lambda_removed_before"]
                )
                assert delta.ge(-1e-7).all()
                rows.append(
                    dict(
                        month=month,
                        original_scenario=a.supply_trial_scenario.iloc[0],
                        network_scenario=ns,
                        area=area,
                        frames=len(g),
                        common_valid=int(common.sum()),
                        became_valid=int(
                            (~g.status_before.eq("valid") & g.status_restored.eq("valid")).sum()
                        ),
                        valid_to_held=int(
                            (g.status_before.eq("valid") & ~g.status_restored.eq("valid")).sum()
                        ),
                        delta_min=delta.min(),
                        delta_median=delta.median(),
                        delta_max=delta.max(),
                        below_to_above=int(
                            (
                                (g.loc[common, "lambda_removed_before"] < 1 - 1e-8)
                                & (g.loc[common, "lambda_removed_restored"] > 1 + 1e-8)
                            ).sum()
                        ),
                        removed_balance_infeasible_to_feasible=int(
                            (
                                g.removed_simultaneous_status_before.eq("infeasible")
                                & g.removed_simultaneous_status_restored.eq("feasible")
                                & g.all_firms_balance_status_before.eq("feasible")
                            ).sum()
                        ),
                    )
                )
        for path in sorted((OUT / "all_firms").glob(month + "__*.parquet")):
            f = pd.read_parquet(path)
            # 全法人不成立を企業除去後の不成立から分離。
            reason = np.select(
                [
                    f.all_firms_status.ne("infeasible"),
                    f.national_net_demand_mw.lt(0),
                    f.national_known_upper_mw.lt(f.national_net_demand_mw),
                ],
                [
                    "not_infeasible",
                    "net_absorption_at_national_level",
                    "limited_national_upper_below_net",
                ],
                default="network_delivery_or_local_absorption",
            )
            f["diagnostic_scope"] = reason
            for key, g in f.groupby(
                [
                    "supply_trial_scenario",
                    "network_scenario",
                    "all_firms_status",
                    "diagnostic_scope",
                ]
            ):
                base.append(
                    dict(
                        zip(
                            [
                                "supply_trial_scenario",
                                "network_scenario",
                                "all_firms_status",
                                "diagnostic_scope",
                            ],
                            key,
                        ),
                        month=month,
                        frames=len(g),
                    )
                )
    pd.DataFrame(rows).to_csv(QA / "record_restoration_pairs_monthly.csv", index=False)
    pd.DataFrame(base).to_csv(QA / "all_firms_monthly.csv", index=False)
    result = dict(
        pair_frames=sum(r["frames"] for r in rows),
        common_valid_frames=sum(r["common_valid"] for r in rows),
        restoration_monotonicity_violations=0,
        join_rate=1.0,
        unmatched=0,
        conditions_added=0,
    )
    (QA / "sensitivity_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    price_summary()
    print(result, flush=True)


if __name__ == "__main__":
    main()
