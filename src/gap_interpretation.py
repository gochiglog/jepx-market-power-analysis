"""固定参照容量差の解釈。元版を変更せずゼロ・欠測・元セルを診断する。"""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.generation_hjks import asset_id, read_raw
from src.time_normalization import parse_date, sha256
from src.unmatched_generation import write_csv

EPOCH = date(2025, 4, 1)


def check_frozen(root, repo):
    frozen = json.loads(
        (repo / "metadata/research/reference_gap_frozen_version_20261016.json").read_text()
    )
    for r in frozen["files"]:
        path = (root if r["location"] == "data_root" else repo) / r["path"]
        if sha256(path) != r["sha256"]:
            raise ValueError("固定版SHA不一致: " + str(path))
    return frozen


def time_label(slot):
    return (datetime(2025, 4, 1) + timedelta(minutes=int(slot) * 30)).isoformat() + "+09:00"


def consecutive_runs(positions, condition):
    """欠測や観測時刻の飛びを橋渡ししない。終了slotはexclusive。"""
    positions = np.asarray(positions)
    condition = np.asarray(condition, dtype=bool)
    if len(positions) != len(condition) or len(set(positions.tolist())) != len(positions):
        raise ValueError("位置と状態の不整合/重複")
    if len(positions) == 0:
        return []
    if np.any(np.diff(positions) <= 0):
        raise ValueError("位置は昇順で一意である必要がある")
    selected = positions[condition]
    if not len(selected):
        return []
    cuts = np.r_[0, np.flatnonzero(np.diff(selected) != 1) + 1, len(selected)]
    return [
        (int(selected[a]), int(selected[b - 1]) + 1, b - a) for a, b in zip(cuts[:-1], cuts[1:])
    ]


def load_fixed_cells(path, mapping):
    ids = pa.array([r["asset_key_candidate"] for r in mapping])
    fuel_ids = {}
    chunks = []
    columns = [
        "asset_key_candidate",
        "delivery_date",
        "period",
        "generation_avg_kw",
        "capacity_reference_kw",
        "capacity_gap_reference_kw",
        "発電方式・燃種",
        "qa_period_contradiction",
        "qa_operation_boundary_day",
    ]
    for b in pq.ParquetFile(path).iter_batches(columns=columns):
        t = pa.Table.from_batches([b])
        idx = pc.index_in(t["asset_key_candidate"], value_set=ids)
        if idx.null_count:
            raise ValueError("固定計算と対応表の未一致")
        fuel = []
        for f in t["発電方式・燃種"].to_pylist():
            if f not in fuel_ids:
                fuel_ids[f] = len(fuel_ids)
            fuel.append(fuel_ids[f])
        chunks.append(
            pd.DataFrame(
                dict(
                    aid=idx.to_numpy().astype(np.int16),
                    slot=np.array(
                        [
                            (d - EPOCH).days * 48 + p - 1
                            for d, p in zip(t["delivery_date"].to_pylist(), t["period"].to_pylist())
                        ],
                        dtype=np.int32,
                    ),
                    gen=pc.cast(t["generation_avg_kw"], pa.float64()).to_numpy(),
                    cap=pc.cast(t["capacity_reference_kw"], pa.float64()).to_numpy(),
                    gap=pc.cast(t["capacity_gap_reference_kw"], pa.float64()).to_numpy(),
                    fuel=np.array(fuel, dtype=np.int8),
                    period_bad=t["qa_period_contradiction"].to_numpy(),
                    boundary=t["qa_operation_boundary_day"].to_numpy(),
                )
            )
        )
    df = pd.concat(chunks, ignore_index=True)
    df["area"] = pd.Categorical(np.array([r["area"] for r in mapping])[df.aid])
    df["zero"] = df.gen.eq(0)
    df["positive"] = df.gen.gt(0)
    df["missing"] = df.gen.isna()
    df["calculable"] = df.gap.notna()
    df["zero_capacity_kw"] = np.where(df.zero & df.calculable, df.cap, 0)
    return df, {v: k for k, v in fuel_ids.items()}


def zero_diagnosis(df, mapping, fuels, reports, output):
    groups = []
    for dimension, column in [("エリア", "area"), ("燃種", "fuel")]:
        for label, d in df.groupby(column, observed=True):
            calc = d[d.calculable]
            z = calc[calc.zero]
            zero_aids = set(z.aid)
            zero_ref = sum(Decimal(mapping[int(i)]["capacity_reference_raw_kw"]) for i in zero_aids)
            denom = calc.gap.sum() if len(calc) else None
            groups.append(
                dict(
                    dimension=dimension,
                    group=str(label) if column == "area" else fuels[int(label)],
                    source_cells=len(d),
                    zero_cells=int(d.zero.sum()),
                    missing_cells=int(d.missing.sum()),
                    calculable_cells=len(calc),
                    calculable_zero_cells=len(z),
                    zero_asset_count=len(zero_aids),
                    zero_asset_distinct_reference_kw=str(zero_ref),
                    zero_capacity_kw_cell_sum=float(z.cap.sum()) if len(calc) else None,
                    reference_gap_kw_cell_sum=float(denom) if denom is not None else None,
                    zero_share_of_gap_sum=float(z.cap.sum() / denom)
                    if denom and denom > 0
                    else None,
                    sum_unit="kW・30分コマ（時間を掛けた電力量ではない）",
                )
            )
    write_csv(reports / "zero_output_fuel_area_20261016.csv", groups)
    states = []
    runs = []
    missing_runs = []
    absent = []
    for aid, d in df.groupby("aid", observed=True):
        a = mapping[int(aid)]
        d = d.sort_values("slot")
        pos = d.slot.to_numpy()
        zero = d.zero.to_numpy()
        positive = d.positive.to_numpy()
        known = (~d.missing).to_numpy()
        state = (
            "全数値観測がゼロ"
            if zero.any() and not positive.any()
            else "ゼロと正の出力を観測"
            if zero.any() and positive.any()
            else "正の出力のみ観測"
            if positive.any()
            else "数値観測なし"
        )
        rr = consecutive_runs(pos, zero)
        for start, end, length in rr:
            runs.append(
                dict(
                    aid=int(aid),
                    asset_key_candidate=a["asset_key_candidate"],
                    area=a["area"],
                    plant_name=a["plant_name"],
                    unit_name=a["unit_name"],
                    start_slot=start,
                    end_slot=end,
                    start_datetime=time_label(start),
                    end_datetime_exclusive=time_label(end),
                    cells=length,
                    hours=length / 2,
                    left_observation_boundary=start == int(pos.min()),
                    right_observation_boundary=end == int(pos.max()) + 1,
                )
            )
        # 状態変化は隣接して観測されたセル同士だけ。間に欠測/不存在があれば数えない。
        adjacent = np.diff(pos) == 1
        transitions = int(
            np.sum(adjacent & ((zero[:-1] & positive[1:]) | (positive[:-1] & zero[1:])))
        )
        states.append(
            dict(
                asset_key_candidate=a["asset_key_candidate"],
                area=a["area"],
                plant_name=a["plant_name"],
                unit_name=a["unit_name"],
                state=state,
                source_cells=len(d),
                zero_cells=int(zero.sum()),
                positive_cells=int(positive.sum()),
                missing_cells=int((~known).sum()),
                zero_positive_transitions=transitions,
                longest_zero_cells=max((r[2] for r in rr), default=0),
                reference_use_status=a["research_use_status"],
                numeric_causes_verified=False,
            )
        )
        if a["area"] == "関西":
            for start, end, length in consecutive_runs(pos, d.missing.to_numpy()):
                missing_runs.append(
                    dict(
                        asset_key_candidate=a["asset_key_candidate"],
                        plant_name=a["plant_name"],
                        unit_name=a["unit_name"],
                        start_datetime=time_label(start),
                        end_datetime_exclusive=time_label(end),
                        missing_cells=length,
                        source="存在するunit日の元空セル",
                    )
                )
            days = np.unique(pos // 48)
            missing_days = sorted(set(range(int(days.min()), int(days.max()) + 1)) - set(days))
            for start, end, length in consecutive_runs(
                np.array(missing_days), np.ones(len(missing_days), dtype=bool)
            ):
                absent.append(
                    dict(
                        asset_key_candidate=a["asset_key_candidate"],
                        plant_name=a["plant_name"],
                        unit_name=a["unit_name"],
                        start_date=str(EPOCH + timedelta(days=start)),
                        end_date_inclusive=str(EPOCH + timedelta(days=end - 1)),
                        absent_unit_days=length,
                        hypothetical_slots=length * 48,
                        interpretation="最初/最後の観測日の間にunit日が存在しない。値を生成/補完しない",
                    )
                )
    write_csv(reports / "zero_output_asset_states_20261016.csv", states)
    pq.write_table(
        pa.Table.from_pylist(runs),
        output / "zero_output_consecutive_runs.parquet",
        compression="zstd",
    )
    write_csv(reports / "kansai_missing_cell_periods_20261016.csv", missing_runs)
    if absent:
        write_csv(reports / "kansai_absent_unit_days_20261016.csv", absent)
    reg = (
        df.groupby(["area", "slot"], observed=True)
        .agg(
            zero_capacity_kw=("zero_capacity_kw", "sum"),
            observed_zero_units=("zero", "sum"),
            calculation_cells=("calculable", "sum"),
            total_gap_partial_sum_kw=("gap", lambda x: x.sum(min_count=1)),
        )
        .reset_index()
    )
    reg.loc[reg.calculation_cells.eq(0), "zero_capacity_kw"] = np.nan
    reg["zero_share_of_partial_sum"] = reg.zero_capacity_kw / reg.total_gap_partial_sum_kw.where(
        reg.total_gap_partial_sum_kw > 0
    )
    pq.write_table(
        pa.Table.from_pandas(reg, preserve_index=False),
        output / "zero_capacity_regional_contributions.parquet",
        compression="zstd",
    )
    kansai = []
    for aid, d in df[df.area == "関西"].groupby("aid", observed=True):
        a = mapping[int(aid)]
        missing = d.missing
        capheld = d.cap.isna()
        kansai.append(
            dict(
                asset_key_candidate=a["asset_key_candidate"],
                plant_name=a["plant_name"],
                unit_name=a["unit_name"],
                observed_cells=len(d),
                raw_empty_cells=int(missing.sum()),
                capacity_held_cells=int(capheld.sum()),
                raw_missing_cap_usable=int((missing & ~capheld).sum()),
                raw_present_cap_held=int((~missing & capheld).sum()),
                both_raw_missing_cap_held=int((missing & capheld).sum()),
                raw_present_cap_usable=int((~missing & ~capheld).sum()),
                first_observed_date=str(EPOCH + timedelta(days=int(d.slot.min() // 48))),
                last_observed_date=str(EPOCH + timedelta(days=int(d.slot.max() // 48))),
                outside_observation_span_not_called_missing_days=int(d.slot.min() // 48)
                + 364
                - int(d.slot.max() // 48),
            )
        )
    write_csv(reports / "kansai_missing_capacity_separation_20261016.csv", kansai)
    return states, runs, kansai, absent


def inspect_raw_cases(root, repo, df, mapping, reports):
    prior = json.loads((reports / "generation_hjks_FY2025_qa.json").read_text())
    directory = Path(
        json.loads((reports / "reference_capacity_gap_qa_20261016.json").read_text())["output_path"]
    ).parent
    negatives = pq.read_table(directory / "negative_gap_cell_index_FY2025.parquet").to_pandas()
    negatives["ratio"] = negatives.gen_micro / negatives.cap_micro
    top = negatives.sort_values(["ratio", "day", "period"], ascending=[False, True, True]).head(5)
    targets = {(int(r.aid), int(r.day)): int(r.period) for r in top.itertuples()}
    index = {r["asset_key_candidate"]: i for i, r in enumerate(mapping)}
    cases = []
    neighbors = []
    raw_kansai = defaultdict(int)
    for source in prior["generation"]["inputs"]:
        headers, rows, _ = read_raw(
            root, {"local_path": source["path"], "sha256": source["sha256"]}
        )
        columns = [h for h in headers if h.endswith("[kWh]") and ":" in h]
        for number, r, _ in rows:
            aid = index[asset_id(r)]
            day = (parse_date(r["対象日"]) - EPOCH).days
            if r["エリア"] == "関西":
                raw_kansai[aid] += sum(r[c] == "" for c in columns)
            if (aid, day) not in targets:
                continue
            p = targets[aid, day]
            a = mapping[aid]
            cap = Decimal(a["capacity_reference_raw_kw"])
            values = [Decimal(r[c]) for c in columns if r[c] != ""]
            sum48 = sum(values, Decimal(0))
            raw_day = Decimal(r["日量[kWh]"]) if r["日量[kWh]"] != "" else None
            cases.append(
                dict(
                    case_id=f"spike_{len(cases) + 1}",
                    asset_key_candidate=a["asset_key_candidate"],
                    area=a["area"],
                    plant_name=r["発電所名"],
                    unit_name=r["ユニット名"],
                    fuel=r["発電方式・燃種"],
                    delivery_date=r["対象日"],
                    period=p,
                    raw_file=source["path"],
                    source_row=number,
                    raw_sha256=source["sha256"],
                    raw_column=columns[p - 1],
                    raw_kwh_30min=r[columns[p - 1]],
                    generation_avg_kw=str(Decimal(r[columns[p - 1]]) * 2),
                    capacity_reference_kw=str(cap),
                    ratio=str(Decimal(r[columns[p - 1]]) * 2 / cap),
                    daily_raw_kwh=r["日量[kWh]"],
                    sum_48_numeric_kwh=str(sum48),
                    daily_sum_matches=raw_day == sum48,
                    raw_unit="kWh/30分",
                    normalization="原値を0.5時間で除す。変更なし",
                    source_unit_update_time=r["更新日時"],
                    equipment_scope="固定診断表の一意unit範囲。発電計測点gross/netは未確認",
                )
            )
            for pp in range(max(1, p - 2), min(48, p + 2) + 1):
                neighbors.append(
                    dict(
                        asset_key_candidate=a["asset_key_candidate"],
                        delivery_date=r["対象日"],
                        period=pp,
                        raw_file=source["path"],
                        source_row=number,
                        raw_column=columns[pp - 1],
                        raw_kwh_30min=r[columns[pp - 1]],
                        generation_avg_kw=str(Decimal(r[columns[pp - 1]]) * 2)
                        if r[columns[pp - 1]]
                        else None,
                    )
                )
    for aid, d in df[df.area == "関西"].groupby("aid", observed=True):
        if raw_kansai[int(aid)] != int(d.missing.sum()):
            raise ValueError("関西元CSV空セル照合不一致")
    cases.sort(key=lambda r: -Decimal(r["ratio"]))
    for i, r in enumerate(cases, 1):
        r["case_id"] = f"spike_{i}"
    write_csv(reports / "top5_exceedance_raw_cases_20261016.csv", cases)
    write_csv(reports / "top5_exceedance_neighbors_20261016.csv", neighbors)
    return cases


def nuclear_stats(df, mapping, reports):
    annual = []
    monthly = []
    for aid, a in enumerate(mapping):
        if not (
            (a["plant_name"] == "高浜発電所" and a["unit_name"] in ("３号機", "４号機"))
            or (a["plant_name"] == "川内原子力発電所" and a["unit_name"] in ("１号機", "２号機"))
        ):
            continue
        d = df[df.aid == aid]
        cap = float(a["capacity_reference_raw_kw"])
        known = d[d.gen.notna()]
        ratio = known.gen / cap
        annual.append(
            dict(
                asset_key_candidate=a["asset_key_candidate"],
                plant_name=a["plant_name"],
                unit_name=a["unit_name"],
                source_generation_kwh=float(known.gen.sum() / 2),
                reference_capacity_kw=cap,
                negative_gap_cells=int((d.gap < 0).sum()),
                positive_generation_cells=int(d.positive.sum()),
                max_ratio=float(ratio.max()),
                ratio_above_104_cells=int((ratio > 1.04).sum()),
                public_thermal_operation="整合し得る候補。全負値の原因確定ではない",
            )
        )
        for month, g in d.groupby(
            d.slot.map(lambda s: (EPOCH + timedelta(days=int(s) // 48)).strftime("%Y-%m"))
        ):
            r = g.loc[g.positive, "gen"] / cap
            monthly.append(
                dict(
                    plant_name=a["plant_name"],
                    unit_name=a["unit_name"],
                    month=month,
                    positive_cells=len(r),
                    negative_cells=int((g.gap < 0).sum()),
                    positive_ratio_median=float(r.median()) if len(r) else None,
                    positive_ratio_p95=float(r.quantile(0.95)) if len(r) else None,
                    max_ratio=float(r.max()) if len(r) else None,
                )
            )
    write_csv(reports / "nuclear_thermal_operation_comparison_20261016.csv", annual)
    write_csv(reports / "nuclear_monthly_output_ratios_20261016.csv", monthly)
    return annual


def grouped_runs_and_missing(df, mapping, runs, fuels, reports):
    """連続ゼロの燃種/エリア集計と関西の月別欠測を独立に残す。"""
    aid_fuels = {int(i): sorted(set(g.fuel)) for i, g in df.groupby("aid", observed=True)}
    if any(len(v) != 1 for v in aid_fuels.values()):
        raise ValueError("unit内燃種変更あり。連続期間の燃種割当は要検討")
    rr = pd.DataFrame(runs)
    rr["fuel_raw"] = rr.aid.map(lambda i: fuels[aid_fuels[int(i)][0]])
    rows = []
    for dimension, column in [("エリア", "area"), ("燃種", "fuel_raw")]:
        for group, g in rr.groupby(column, dropna=False):
            rows.append(
                dict(
                    dimension=dimension,
                    group=group,
                    zero_run_count=len(g),
                    zero_cells=int(g.cells.sum()),
                    max_continuous_zero_cells=int(g.cells.max()),
                    median_run_cells=float(g.cells.median()),
                    p95_run_cells=float(g.cells.quantile(0.95)),
                    runs_at_least_7days=int((g.cells >= 7 * 48).sum()),
                    runs_at_least_30days=int((g.cells >= 30 * 48).sum()),
                    observed_unit_count=int(g.aid.nunique()),
                    boundary_runs=int(
                        (g.left_observation_boundary | g.right_observation_boundary).sum()
                    ),
                    cause="観測状態のみ。停止/利用可能の認定ではない",
                )
            )
    write_csv(reports / "zero_run_fuel_area_summary_20261016.csv", rows)
    states = pd.read_csv(reports / "zero_output_asset_states_20261016.csv", keep_default_na=False)
    key_to_aid = {a["asset_key_candidate"]: i for i, a in enumerate(mapping)}
    states["fuel_raw"] = states.asset_key_candidate.map(
        lambda key: fuels[aid_fuels[key_to_aid[key]][0]]
    )
    write_csv(reports / "zero_output_asset_states_20261016.csv", states.to_dict("records"))
    state_groups = []
    for dimension, column in [("エリア", "area"), ("燃種", "fuel_raw")]:
        for (group, state), g in states.groupby([column, "state"], dropna=False):
            state_groups.append(
                dict(
                    dimension=dimension,
                    group=group,
                    state=state,
                    observed_units=len(g),
                    zero_cells=int(g.zero_cells.sum()),
                    missing_cells=int(g.missing_cells.sum()),
                    max_zero_cells=int(g.longest_zero_cells.max()),
                    note="観測状態の分類。原因・停止認定・利用可能認定ではない",
                )
            )
    write_csv(reports / "zero_state_fuel_area_20261016.csv", state_groups)
    k = df[df.area == "関西"].copy()
    k["month"] = [(EPOCH + timedelta(days=int(s) // 48)).strftime("%Y-%m") for s in k.slot]
    k["raw_missing_cap_usable"] = k.missing & k.cap.notna()
    k["raw_missing_cap_held"] = k.missing & k.cap.isna()
    k["raw_present_cap_held"] = ~k.missing & k.cap.isna()
    grouped = (
        k.groupby(["aid", "month"], observed=True)
        .agg(
            observed_cells=("slot", "size"),
            raw_empty_cells=("missing", "sum"),
            raw_missing_cap_usable=("raw_missing_cap_usable", "sum"),
            raw_missing_cap_held=("raw_missing_cap_held", "sum"),
            raw_present_cap_held=("raw_present_cap_held", "sum"),
        )
        .reset_index()
    )
    grouped["asset_key_candidate"] = grouped.aid.map(
        lambda i: mapping[int(i)]["asset_key_candidate"]
    )
    grouped["plant_name"] = grouped.aid.map(lambda i: mapping[int(i)]["plant_name"])
    grouped["unit_name"] = grouped.aid.map(lambda i: mapping[int(i)]["unit_name"])
    write_csv(reports / "kansai_missing_unit_month_20261016.csv", grouped.to_dict("records"))
