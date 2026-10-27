"""保存翌々日包絡を切替前/新フェンス公表後に分け、旧残存行を重複使用しない。"""

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.net_demand_network import EDGES, FENCE_COEFFICIENTS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/interim/occto/annual_diagnostic/20261027_network_v1"


def run():
    if OUTPUT.exists():
        raise ValueError("既存版を上書きしない")
    OUTPUT.mkdir(parents=True)
    path = (
        ROOT
        / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1"
        / "network_candidate_bounds.parquet"
    )
    f = pq.read_table(path).to_pandas()
    constraints = []
    states = []
    excluded = []
    for (d, p), g in f.groupby(["delivery_date", "period"], sort=True):
        lookup = {(r.link_name_raw, r.direction_raw): r for r in g.itertuples()}
        assert len(lookup) == len(g)
        if d < date(2026, 3, 13):
            names = [e[0] for e in EDGES] + ["北陸フェンス"]
            version = "legacy_11"
        else:
            names = [e[0] for i, e in enumerate(EDGES) if i not in [3, 4, 5]] + [
                "中部フェンス",
                "北陸フェンス",
                "関西フェンス",
            ]
            version = "published_fences_10"
        rows = []
        reason = []
        for name in names:
            pair = [lookup.get((name, x)) for x in ["順方向", "逆方向"]]
            if any(x is None for x in pair):
                reason.append("missing_published_fence_or_link:" + name)
                continue
            bounds = []
            for r in pair:
                vals = [r.operating_capacity_mw, r.margin_mw, r.wide_area_adjustment_mw]
                bounds.append(None if any(v is None for v in vals) else vals[0] - vals[1] - vals[2])
            if any(v is None for v in bounds):
                reason.append("missing_bound:" + name)
                continue
            hi, lo = bounds
            if lo > hi:
                reason.append("inconsistent_bounds:" + name)
                continue
            coeff = FENCE_COEFFICIENTS.get(name)
            if coeff is None:
                coeff = [int(e[0] == name) for e in EDGES]
            rows.append(
                dict(
                    delivery_date=d,
                    period=int(p),
                    constraint_name=name,
                    coefficient_version=version,
                    coefficients_json=json.dumps(list(coeff)),
                    lower_mw=lo,
                    upper_mw=hi,
                    source_ids_json=json.dumps([r.source_record_id for r in pair]),
                    source_file=pair[0].source_file,
                    bound_formula="C_signed-M_signed-W_signed",
                    legacy_internal_rows_used_as_independent_bounds=False
                    if version == "published_fences_10"
                    else True,
                )
            )
        if d >= date(2026, 3, 13):
            for (n, direction), r in lookup.items():
                if n in [EDGES[i][0] for i in [3, 4, 5]]:
                    excluded.append(
                        dict(
                            delivery_date=d,
                            period=int(p),
                            link_name_raw=n,
                            direction_raw=direction,
                            source_record_id=r.source_record_id,
                            reason="legacy_display_row_not_independent_capacity",
                        )
                    )
        state = "ready" if not reason else "held_missing_cutover_constraints"
        states.append(
            dict(
                delivery_date=d,
                period=int(p),
                network_input_status=state,
                coefficient_version=version,
                reasons_json=json.dumps(reason, ensure_ascii=False),
                known_constraint_rows=len(rows),
            )
        )
        if not reason:
            constraints.extend(rows)
    c = pd.DataFrame(constraints)
    s = pd.DataFrame(states)
    assert len(s) == 17520 and not s.duplicated(["delivery_date", "period"]).any()
    assert not c.duplicated(["delivery_date", "period", "constraint_name"]).any()
    c.to_parquet(OUTPUT / "annual_network_constraints.parquet", index=False)
    s.to_parquet(OUTPUT / "annual_network_status.parquet", index=False)
    pd.DataFrame(excluded).to_parquet(
        OUTPUT / "legacy_display_rows_retained_not_applied.parquet", index=False
    )
    s.groupby(["coefficient_version", "network_input_status"]).size().rename(
        "time_keys"
    ).reset_index().to_csv(ROOT / "reports/27_fy2025_annual/qa/network_scope.csv", index=False)
    # 切替前は旧固定値・係数・元行参照を独立に全照合。
    old = pd.read_parquet(
        ROOT
        / "data/interim/occto/net_demand_network/20261026_diagnostic_v1"
        / "legacy_network_constraints_FY2025.parquet"
    )
    key = ["delivery_date", "period", "constraint_name"]
    old.delivery_date = pd.to_datetime(old.delivery_date).dt.date
    a = old.merge(
        c[c.coefficient_version == "legacy_11"],
        on=key,
        validate="one_to_one",
        how="outer",
        indicator=True,
        suffixes=("_old", "_annual"),
    )
    assert len(a) == 182688 and a._merge.eq("both").all()
    for col in ["lower_mw", "upper_mw"]:
        assert a[col + "_old"].equals(a[col + "_annual"])
    assert all(
        np.array_equal(json.loads(x), json.loads(y))
        for x, y in zip(a.coefficients_json_old, a.coefficients_json_annual, strict=True)
    )
    audit = dict(
        total_times=17520,
        ready_times=int(s.network_input_status.eq("ready").sum()),
        held_times=int(s.network_input_status.ne("ready").sum()),
        legacy_times=16608,
        original_legacy_constraints_equal=182688,
        legacy_join_rate=1.0,
        unmatched=0,
        new_fence_model="10補助輸送変数を保持。中央3地域は純移送カット3本で管理、旧3辺の独立上限は使わない",
        source_evidence="保存公式system_cutover_2026.pdf物理2/5、central_loop.pdf物理7/8",
        actual_physical_switch_instant_inferred=False,
        postcutover_contract="対象コマに新3フェンス公表行が揃う場合のみ使用。予定ルート断を実施認定せず、地域間経路を切断しない",
    )
    (ROOT / "reports/27_fy2025_annual/qa/network_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    print(audit)


if __name__ == "__main__":
    run()
