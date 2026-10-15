"""承認された一定参照容量シナリオの診断。停止補正・最終baselineではない。"""

import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.asset_analysis_readiness import load_csv
from src.generation_hjks import normalize_name, read_raw
from src.time_normalization import sha256
from src.unmatched_generation import plant_stem, unit_token, write_csv

D = pa.decimal128(38, 6)
SMALL = pa.decimal128(32, 6)
SCALE = Decimal(1000000)


def equipment_signature(unit, plant):
    text = normalize_name(unit)
    stem = normalize_name(plant_stem(plant))
    if stem and text.startswith(stem):
        text = text[len(stem) :]
    # 全ての番号と新旧・GT/ST・燃種識別を保持。最後の軸番号だけでは比べない。
    return (
        tuple(re.findall(r"\d+", text)),
        tuple(re.findall(r"新|旧|GT[A-Z]*|ST[A-Z]*|LNG|石炭", text.upper())),
    )


def scope_equal(a, h):
    return (
        a["area"] == h["エリア"]
        and plant_stem(a["plant_name"]) == plant_stem(h["発電所名"])
        and unit_token(a["unit_name"]) == unit_token(h["ユニット名"])
        and equipment_signature(a["unit_name"], a["plant_name"])
        == equipment_signature(h["ユニット名"], h["発電所名"])
    )


def period_flags(day, start, end):
    """稼働日を容量有効期間へ転用しない。矛盾・境界だけを保守的に保留する。"""
    s, e = start.replace("/", "-"), end.replace("/", "-")
    before = bool(s and s != "1900-01-01" and day < s)
    after = bool(e and e != "9999-12-31" and day > e)
    boundary = bool(
        (s and s != "1900-01-01" and day == s) or (e and e != "9999-12-31" and day == e)
    )
    return before or after, boundary


def build_mapping(assets, evidence, gaps, master, snapshot):
    proposals = defaultdict(list)
    notation = []
    for a in assets:
        if a["unit_match"] == "True":
            proposals[a["asset_key_candidate"]].append(
                (a["candidate_snapshot_rows"], "strict", "not_assessed")
            )
    for e in evidence:
        proposals[e["asset_key_candidate"]].append(
            (e["hjks_snapshot_row_id"], "candidate49", e["verification_status"])
        )
    by_asset = {a["asset_key_candidate"]: a for a in assets}
    for gap in gaps:
        if gap["gap_category"] != "表記の違い":
            continue
        a = by_asset[gap["asset_key_candidate"]]
        pool = [i for i in a["plant_candidate_snapshot_rows"].split("|") if i]
        targets = [
            i
            for i in pool
            if master[i]["発電所コード"] == a["plant_code"] and scope_equal(a, master[i])
        ]
        notation.append(
            dict(
                asset_key_candidate=a["asset_key_candidate"],
                plant_code=a["plant_code"],
                plant_name=a["plant_name"],
                unit_name=a["unit_name"],
                target_ids_json=json.dumps(targets),
                target_count=len(targets),
                review_basis="同code/area・名称基部・号機/系列/軸/新旧token保持。一意候補のみ。fuzzy不使用",
            )
        )
        if len(targets) == 1:
            proposals[a["asset_key_candidate"]].append((targets[0], "notation34", "candidate"))
    reuse = defaultdict(set)
    for aid, choices in proposals.items():
        for target, _, _ in choices:
            reuse[target].add(aid)
    selected_targets = {t for choices in proposals.values() for t, _, _ in choices}
    mapping = []
    for a in assets:
        aid = a["asset_key_candidate"]
        choices = proposals[aid]
        target, route, verification = (
            choices[0] if len(choices) == 1 else ("", "none", "unresolved")
        )
        reason = "使用可（診断限定）"
        if len(choices) != 1:
            reason = "未対応または複数候補"
        elif len(reuse[target]) != 1:
            reason = "同一targetの重複使用"
        elif not scope_equal(a, master[target]):
            reason = "設備範囲・名称が未確認"
        h = master.get(target, {})
        cap = Decimal(h["認可出力"]) if h.get("認可出力") else None
        if target and (cap is None or cap <= 0):
            reason = "正の参照容量を一意に指定できない"
        block_check = "対象外"
        if a["plant_code"] == "48193" and target:
            component_ids = {
                i
                for i, r in master.items()
                if r["発電所コード"] == "4B193"
                and (unit_token(r["ユニット名"]) or "").startswith("7-2")
            }
            ok = (
                h["発電所コード"] == "42393"
                and unit_token(h["ユニット名"]) == "7-2"
                and not component_ids.intersection(selected_targets)
                and scope_equal(a, h)
            )
            block_check = (
                "7-2ブロックを一度だけ参照しGT/ST機器を選択先に含めない" if ok else "条件未確認"
            )
            if not ok:
                reason = "西名古屋7-2ブロック非重複条件未確認"
        dates = json.loads(a["observed_dates"])
        entirely_outside = bool(
            target and all(period_flags(d, h["稼働開始日"], h["稼働終了日"])[0] for d in dates)
        )
        if entirely_outside and reason == "使用可（診断限定）":
            reason = "全観測日が台帳稼働日の範囲外。原値は保持"
        mapping.append(
            dict(
                **{
                    k: a[k]
                    for k in [
                        "asset_key_candidate",
                        "area",
                        "plant_code",
                        "plant_name",
                        "unit_name",
                        "generation_sum",
                    ]
                },
                exploratory_plant_match=a["plant_match"],
                exploratory_plant_is_capacity_join_permission=False,
                target_snapshot_row_id=target,
                diagnostic_mapping_route=route,
                official_verification_status=verification,
                research_use_status="use_diagnostic" if reason == "使用可（診断限定）" else "held",
                research_use_reason=reason,
                equipment_scope_checked=reason == "使用可（診断限定）",
                block_component_check=block_check,
                capacity_reference_raw_kw=str(cap) if cap is not None else "",
                capacity_reference_source_file=snapshot["local_path"],
                capacity_reference_source_sha256=snapshot["sha256"],
                capacity_snapshot_downloaded_at=snapshot.get("downloaded_at"),
                capacity_snapshot_registered_at=snapshot.get("registered_at"),
                capacity_snapshot_download_time_known=snapshot.get("downloaded_at") is not None,
                operation_start_raw=h.get("稼働開始日", ""),
                operation_end_raw=h.get("稼働終了日", ""),
                capacity_constant_assumption=True,
                capacity_effective_period_verified=False,
                metering_gross_net_verified=False,
                final_baseline_adopted=False,
            )
        )
    index = {r["asset_key_candidate"]: r for r in mapping}
    for r in notation:
        r.update(
            research_use_status=index[r["asset_key_candidate"]]["research_use_status"],
            research_use_reason=index[r["asset_key_candidate"]]["research_use_reason"],
        )
    return mapping, notation


def add_columns(batch, mapping):
    ids = batch.column(batch.schema.get_field_index("asset_key_candidate"))
    if len({r["asset_key_candidate"] for r in mapping}) != len(mapping):
        raise ValueError("対応表のsource重複")
    right = pa.Table.from_pylist(mapping)
    idx = pc.index_in(ids, value_set=right["asset_key_candidate"])
    if idx.null_count:
        raise ValueError("元行のassetが対応表から脱落")
    selected = right.take(idx)
    days = [
        d.isoformat()
        for d in batch.column(batch.schema.get_field_index("delivery_date")).to_pylist()
    ]
    starts = selected["operation_start_raw"].to_pylist()
    ends = selected["operation_end_raw"].to_pylist()
    flags = [period_flags(d, s, e) for d, s, e in zip(days, starts, ends)]
    period_bad = pa.array([r[0] for r in flags])
    boundary = pa.array([r[1] for r in flags])
    accepted = np.array(selected["research_use_status"].to_pylist()) == "use_diagnostic"
    cap_raw = [Decimal(v) if v else None for v in selected["capacity_reference_raw_kw"].to_pylist()]
    cap = pa.array(
        [
            v if ok and not bad and not edge else None
            for v, ok, (bad, edge) in zip(cap_raw, accepted, flags)
        ],
        type=D,
    )
    gen = batch.column(batch.schema.get_field_index("generation_kwh_30min"))
    avg = pc.cast(
        pc.multiply(pc.cast(gen, SMALL), pa.scalar(Decimal(2), type=pa.decimal128(1, 0))), D
    )
    gap = pc.cast(pc.subtract(pc.cast(cap, SMALL), pc.cast(avg, SMALL)), D)
    negative = pc.fill_null(pc.less(gap, pa.scalar(Decimal(0), type=D)), False)
    table = pa.Table.from_batches([batch])
    for name, array in [
        ("generation_avg_kw", avg),
        ("capacity_reference_kw", cap),
        ("capacity_gap_reference_kw", gap),
        ("qa_generation_missing", pc.is_null(gen)),
        ("qa_capacity_unavailable", pc.is_null(cap)),
        ("qa_period_contradiction", period_bad),
        ("qa_operation_boundary_day", boundary),
        ("qa_negative_reference_gap", negative),
        ("qa_generation_exceeds_reference", negative),
    ]:
        table = table.append_column(name, array)
    for name in [
        "target_snapshot_row_id",
        "diagnostic_mapping_route",
        "official_verification_status",
        "research_use_status",
        "research_use_reason",
        "capacity_reference_source_file",
        "capacity_reference_source_sha256",
        "capacity_snapshot_registered_at",
        "capacity_snapshot_download_time_known",
        "capacity_constant_assumption",
        "capacity_effective_period_verified",
        "block_component_check",
    ]:
        table = table.append_column(name, selected[name])
    # 取得時点不明を登録日時で代用しない。Arrow null型を明示的な文字列nullable列へする。
    table = table.append_column(
        "capacity_snapshot_downloaded_at",
        pa.array(selected["capacity_snapshot_downloaded_at"].to_pylist(), type=pa.string()),
    )
    table = table.replace_schema_metadata(
        {
            **(table.schema.metadata or {}),
            b"source_scope": (table.schema.metadata or {}).get(b"scope", b""),
            b"scope": "参照容量と30分平均発電出力の差。診断限定・停止補正なし".encode(),
            b"capacity_assumption": b"constant snapshot reference; effective period unverified",
            b"generation_avg_kw_formula": b"generation_kwh_30min / 0.5 hour",
        }
    )
    return table, idx


def numeric(array):
    return pc.cast(array, pa.float64()).to_numpy(zero_copy_only=False)


def micro(array):
    scaled = pc.multiply(
        pc.cast(pc.fill_null(array, pa.scalar(Decimal(0), type=D)), pa.decimal128(28, 6)),
        pa.scalar(SCALE, type=pa.decimal128(7, 0)),
    )
    return pc.cast(scaled, pa.int64()).to_numpy(zero_copy_only=False)


def stats(values):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    if not len(a):
        return dict(
            count=0,
            min=None,
            p01=None,
            p05=None,
            median=None,
            p95=None,
            p99=None,
            max=None,
            mean=None,
        )
    points = np.quantile(a, [0, 0.01, 0.05, 0.5, 0.95, 0.99, 1])
    return dict(
        count=len(a),
        **dict(zip(["min", "p01", "p05", "median", "p95", "p99", "max"], map(float, points))),
        mean=float(a.mean()),
    )


def coverage_table(df, mapping):
    result = []
    route = np.array([r["diagnostic_mapping_route"] for r in mapping])
    use = np.array([r["research_use_status"] == "use_diagnostic" for r in mapping])
    for stage, allowed in [
        ("strict_scope_checked", {"strict"}),
        ("plus49_scope_checked", {"strict", "candidate49"}),
        ("plus_notation_scope_checked", {"strict", "candidate49", "notation34"}),
    ]:
        selected = use[df.aid.to_numpy()] & np.isin(route[df.aid.to_numpy()], list(allowed))
        for dimension, key in [
            ("全国", np.repeat("全国", len(df))),
            ("エリア", df.area),
            ("月", df.month),
        ]:
            g = pd.DataFrame(
                dict(
                    group=key,
                    amount=df.gen_micro,
                    selected=np.where(selected, df.gen_micro, 0),
                    calc=np.where(selected & df.valid, df.gen_micro, 0),
                    rows=1,
                    matched=selected.astype(int),
                    calculated=(selected & df.valid).astype(int),
                )
            )
            sums = g.groupby("group", observed=True).sum()
            for label, r in sums.iterrows():
                result.append(
                    dict(
                        scenario=stage,
                        dimension=dimension,
                        group=label,
                        total_observed_generation_kwh=str(Decimal(int(r.amount)) / SCALE / 2),
                        scope_matched_generation_kwh=str(Decimal(int(r.selected)) / SCALE / 2),
                        calculable_generation_kwh=str(Decimal(int(r.calc)) / SCALE / 2),
                        unit_generation_coverage=float(r.selected / r.amount) if r.amount else None,
                        calculable_generation_coverage=float(r.calc / r.amount)
                        if r.amount
                        else None,
                        unmatched_observed_generation_kwh=str(
                            Decimal(int(r.amount - r.selected)) / SCALE / 2
                        ),
                        source_rows=int(r.rows),
                        scope_matched_rows=int(r.matched),
                        calculable_cells=int(r.calculated),
                    )
                )
    return result


def regional_partials(df, fixed_ids):
    frames = []
    for scenario, mask in [
        ("observed_calculation_scope", np.ones(len(df), dtype=bool)),
        ("fixed_complete_fy_set", df.aid.isin(fixed_ids).to_numpy()),
    ]:
        d = df[mask].copy()
        if d.empty:
            continue
        d["capacity_known"] = d.cap_valid.astype(int)
        d["gap_known"] = d.valid.astype(int)
        d["cap_on_computed"] = np.where(d.valid, d.cap_micro, 0)
        d["output_on_computed"] = np.where(d.valid, d.gen_micro, 0)
        g = (
            d.groupby(["area", "day", "period"], observed=True)
            .agg(
                observed_source_unit_count=("aid", "size"),
                calculation_scope_unit_count=("capacity_known", "sum"),
                computed_unit_count=("gap_known", "sum"),
                reference_micro=("cap_micro", "sum"),
                computed_reference_micro=("cap_on_computed", "sum"),
                output_micro=("output_on_computed", "sum"),
                gap_micro=("gap_micro", "sum"),
            )
            .reset_index()
        )
        g["scenario"] = scenario
        g["missing_output_in_scope"] = g.calculation_scope_unit_count - g.computed_unit_count
        frames.append(g)
    # 固定集合がないエリアも行として残す。未知の値は0にしない。
    dynamic = frames[0]
    fixed = frames[1] if len(frames) > 1 else dynamic.iloc[:0].copy()
    fixed = dynamic[["area", "day", "period"]].merge(
        fixed, on=["area", "day", "period"], how="left", validate="one_to_one"
    )
    fixed["scenario"] = "fixed_complete_fy_set"
    out = pd.concat([dynamic, fixed], ignore_index=True)
    rows = []
    for r in out.to_dict("records"):
        count = 0 if pd.isna(r["computed_unit_count"]) else int(r["computed_unit_count"])
        scope = (
            0
            if pd.isna(r["calculation_scope_unit_count"])
            else int(r["calculation_scope_unit_count"])
        )

        def value(k, valid):
            return Decimal(int(r[k])) / SCALE if valid else None

        rows.append(
            dict(
                area=r["area"],
                delivery_date=date(2025, 4, 1) + pd.Timedelta(days=int(r["day"])),
                period=int(r["period"]),
                scenario=r["scenario"],
                scope_label="計算対象設備の部分和",
                is_region_total=False,
                observed_source_unit_count=None
                if pd.isna(r["observed_source_unit_count"])
                else int(r["observed_source_unit_count"]),
                calculation_scope_unit_count=scope,
                computed_unit_count=count,
                missing_output_in_scope=scope - count,
                known_reference_capacity_kw=value("reference_micro", scope > 0),
                computed_subset_reference_capacity_kw=value("computed_reference_micro", count > 0),
                generation_avg_partial_sum_kw=value("output_micro", count > 0),
                reference_gap_partial_sum_kw=value("gap_micro", count > 0),
                scope_complete_partial_sum_kw=value("gap_micro", scope > 0 and scope == count),
            )
        )
    table = pa.Table.from_pylist(rows)
    for col in [
        "known_reference_capacity_kw",
        "computed_subset_reference_capacity_kw",
        "generation_avg_partial_sum_kw",
        "reference_gap_partial_sum_kw",
        "scope_complete_partial_sum_kw",
    ]:
        table = table.set_column(
            table.schema.get_field_index(col), col, pa.array([r[col] for r in rows], type=D)
        )
    return table, rows


def run(root: Path, repo: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    reports = repo / "reports"
    prior = json.loads((reports / "generation_hjks_FY2025_qa.json").read_text())
    assets = load_csv(reports / "generation_hjks_677_mapping_candidates.csv")
    evidence = load_csv(reports / "top10_official_mapping_evidence_20261016.csv")
    gaps = load_csv(reports / "plant_unit_coverage_gap_20261016.csv")
    snapshot = prior["snapshots"]["hjks_unit_677"]["source"]
    _, raw, _ = read_raw(root, snapshot)
    master = {f"{snapshot['sha256']}:{n}": r for n, r, _ in raw}
    mapping, notation = build_mapping(assets, evidence, gaps, master, snapshot)
    write_csv(reports / "reference_gap_analysis_mapping_20261016.csv", mapping)
    write_csv(reports / "notation34_explicit_mapping_20261016.csv", notation)
    source = Path(prior["generation"]["output_path"])
    if sha256(source) != prior["generation"]["output_sha256"]:
        raise ValueError("既存long SHA不一致")
    source_hash = sha256(source)
    writer = None
    chunks = []
    source_columns = pq.ParquetFile(source).schema_arrow.names
    destination = output / "reference_capacity_gap_FY2025.parquet"
    for batch in pq.ParquetFile(source).iter_batches(batch_size=65536):
        table, idx = add_columns(batch, mapping)
        if (
            not table.select(source_columns)
            .replace_schema_metadata(batch.schema.metadata)
            .equals(pa.Table.from_batches([batch]))
        ):
            raise ValueError("元列の変更")
        if writer is None:
            writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
        writer.write_table(table)
        day = np.array(
            [(d - date(2025, 4, 1)).days for d in table["delivery_date"].to_pylist()],
            dtype=np.int16,
        )
        aid = np.array(idx.to_pylist(), dtype=np.int16)
        gap = table["capacity_gap_reference_kw"]
        gen = table["generation_avg_kw"]
        cap = table["capacity_reference_kw"]
        chunks.append(
            pd.DataFrame(
                dict(
                    aid=aid,
                    day=day,
                    period=np.array(table["period"].to_pylist(), dtype=np.int8),
                    gen_micro=micro(gen),
                    gap_micro=micro(gap),
                    cap_micro=micro(cap),
                    valid=pc.is_valid(gap).to_numpy(),
                    cap_valid=pc.is_valid(cap).to_numpy(),
                    gen_valid=pc.is_valid(gen).to_numpy(),
                    period_bad=np.array(table["qa_period_contradiction"].to_pylist()),
                    boundary=np.array(table["qa_operation_boundary_day"].to_pylist()),
                )
            )
        )
    writer.close()
    # Parquet全行の元列と数式を独立再検査。入力行数を維持。
    output_pf = pq.ParquetFile(destination)
    count = 0
    for b, rowgroup in zip(
        pq.ParquetFile(source).iter_batches(batch_size=65536), range(output_pf.num_row_groups)
    ):
        saved = output_pf.read_row_group(rowgroup)
        expected, _ = add_columns(b, mapping)
        if not saved.equals(expected):
            raise ValueError("Parquet全セル/型の再読込不一致")
        count += len(saved)
    if count != prior["generation"]["long_rows"] or output_pf.metadata.num_rows != count:
        raise ValueError("行増殖または脱落")
    df = pd.concat(chunks, ignore_index=True)
    df["area"] = pd.Categorical(np.array([r["area"] for r in mapping])[df.aid])
    df["month"] = pd.Categorical(
        np.array(
            [(date(2025, 4, 1) + pd.Timedelta(days=int(d))).strftime("%Y-%m") for d in range(365)]
        )[df.day]
    )
    if df.duplicated(["aid", "day", "period"]).any():
        raise ValueError("元キー重複")
    coverage = coverage_table(df, mapping)
    write_csv(reports / "reference_gap_coverage_20261016.csv", coverage)
    by_asset = df.groupby("aid", observed=True).agg(
        rows=("valid", "size"), valid=("valid", "sum"), gen_present=("gen_valid", "sum")
    )
    fixed_ids = set(by_asset.index[(by_asset.rows == 17520) & (by_asset.valid == 17520)])
    regional, regional_rows = regional_partials(df, fixed_ids)
    pq.write_table(
        regional, output / "reference_gap_regional_partial_sums_FY2025.parquet", compression="zstd"
    )
    if not pq.read_table(output / "reference_gap_regional_partial_sums_FY2025.parquet").equals(
        regional
    ):
        raise ValueError("地域部分和の再読込不一致")
    distributions = []
    unit_month = []
    for (aid, month), d in df.groupby(["aid", "month"], observed=True):
        a = mapping[int(aid)]
        valid = d[d.valid]
        neg = valid[valid.gap_micro < 0]
        unit_month.append(
            dict(
                asset_key_candidate=a["asset_key_candidate"],
                area=a["area"],
                plant_code=a["plant_code"],
                plant_name=a["plant_name"],
                unit_name=a["unit_name"],
                month=str(month),
                source_cells=len(d),
                calculable_cells=len(valid),
                generation_missing_cells=int((~d.gen_valid).sum()),
                negative_gap_cells=len(neg),
                min_gap_kw=float(valid.gap_micro.min() / 1e6) if len(valid) else None,
                max_generation_to_reference_ratio=float((valid.gen_micro / valid.cap_micro).max())
                if len(valid)
                else None,
                period_contradiction_cells=int(d.period_bad.sum()),
                positive_output_after_period_conflict_cells=int(
                    (d.period_bad & (d.gen_micro > 0) & d.gen_valid).sum()
                ),
                boundary_cells=int(d.boundary.sum()),
            )
        )
    write_csv(reports / "reference_gap_unit_month_qa_20261016.csv", unit_month)
    for dimension, key in [
        ("全国", np.repeat("全国", len(df))),
        ("エリア", df.area),
        ("月", df.month),
    ]:
        for label, d in df.groupby(key, observed=True):
            v = d.loc[d.valid, "gap_micro"].to_numpy() / 1e6
            distributions.append(
                dict(
                    dimension=dimension,
                    group=str(label),
                    unit="kW",
                    negative_cells=int((v < 0).sum()),
                    **stats(v),
                )
            )
    write_csv(reports / "reference_gap_distribution_20261016.csv", distributions)
    monthly = []
    rr = pd.DataFrame([{**r, "month": r["delivery_date"].strftime("%Y-%m")} for r in regional_rows])
    for (scenario, area, month), g in rr.groupby(["scenario", "area", "month"], observed=True):
        vals = [float(v) for v in g.reference_gap_partial_sum_kw if v is not None]
        monthly.append(
            dict(
                scenario=scenario,
                area=str(area),
                month=str(month),
                scope_label="計算対象設備の部分和",
                min_computed_unit_count=int(g.computed_unit_count.min()),
                max_computed_unit_count=int(g.computed_unit_count.max()),
                min_scope_unit_count=int(g.calculation_scope_unit_count.min()),
                max_scope_unit_count=int(g.calculation_scope_unit_count.max()),
                missing_generation_cell_total=int(g.missing_output_in_scope.sum()),
                min_known_reference_kw=min(
                    (float(v) for v in g.known_reference_capacity_kw if v is not None), default=None
                ),
                max_known_reference_kw=max(
                    (float(v) for v in g.known_reference_capacity_kw if v is not None), default=None
                ),
                **stats(vals),
            )
        )
    write_csv(reports / "reference_gap_regional_month_partial_20261016.csv", monthly)
    negatives = df[df.valid & (df.gap_micro < 0)].copy()
    negatives["asset_key_candidate"] = np.array([r["asset_key_candidate"] for r in mapping])[
        negatives.aid
    ]
    pq.write_table(
        pa.Table.from_pandas(negatives, preserve_index=False),
        output / "negative_gap_cell_index_FY2025.parquet",
        compression="zstd",
    )
    q = dict(
        source_rows=count,
        output_rows=count,
        original_columns_preserved=True,
        duplicate_key_count=0,
        row_drop=0,
        row_growth=0,
        raw_generation_missing_cells=int((~df.gen_valid).sum()),
        calculable_cells=int(df.valid.sum()),
        negative_gap_cells=len(negatives),
        generation_exceeds_reference_cells=len(negatives),
        capacity_unavailable_cells=int((~df.cap_valid).sum()),
        period_contradiction_cells=int(df.period_bad.sum()),
        positive_output_after_period_conflict_cells=int(
            (df.period_bad & (df.gen_micro > 0) & df.gen_valid).sum()
        ),
        operation_boundary_held_cells=int(df.boundary.sum()),
        mapping_use_counts=dict(Counter(r["research_use_status"] for r in mapping)),
        mapping_route_use_counts=dict(
            Counter(
                r["diagnostic_mapping_route"]
                for r in mapping
                if r["research_use_status"] == "use_diagnostic"
            )
        ),
        notation34_used=sum(r["research_use_status"] == "use_diagnostic" for r in notation),
        fixed_complete_asset_count=len(fixed_ids),
        fixed_asset_keys=[mapping[i]["asset_key_candidate"] for i in sorted(fixed_ids)],
        fixed_observed_generation_coverage=float(
            df.loc[df.aid.isin(fixed_ids), "gen_micro"].sum() / df.gen_micro.sum()
        ),
        national_distribution=next(r for r in distributions if r["dimension"] == "全国"),
        national_coverage=[r for r in coverage if r["dimension"] == "全国"],
        output_path=str(destination),
        output_sha256=sha256(destination),
        source_long_sha256=source_hash,
        source_long_unchanged=sha256(source) == source_hash,
        parquet_roundtrip_equal=True,
        source_snapshot=snapshot,
        capacity_constant_assumption=True,
        capacity_effective_period_verified=False,
        stop_data_applied=False,
        final_baseline_adopted=False,
        inquiry_work_in_scope=False,
    )
    (reports / "reference_capacity_gap_qa_20261016.json").write_text(
        json.dumps(q, ensure_ascii=False, indent=2) + "\n"
    )
    return q


def summarize_result(repo: Path):
    """保存済み診断結果から超過集中・固定集合・ゼロ出力条件の分布を再現する。"""
    reports = repo / "reports"
    q = json.loads((reports / "reference_capacity_gap_qa_20261016.json").read_text())
    mapping = load_csv(reports / "reference_gap_analysis_mapping_20261016.csv")
    directory = Path(q["output_path"]).parent
    negative = pq.read_table(directory / "negative_gap_cell_index_FY2025.parquet").to_pandas()
    ranking = []
    for aid, g in negative.groupby("aid"):
        a = mapping[int(aid)]
        ordered = g.sort_values(["day", "period"])
        first, last = ordered.iloc[0], ordered.iloc[-1]
        ranking.append(
            dict(
                asset_key_candidate=a["asset_key_candidate"],
                area=a["area"],
                plant_name=a["plant_name"],
                unit_name=a["unit_name"],
                negative_cells=len(g),
                min_reference_gap_kw=float(g.gap_micro.min() / 1e6),
                max_generation_to_reference_ratio=float((g.gen_micro / g.cap_micro).max()),
                first_negative_date=str(date(2025, 4, 1) + pd.Timedelta(days=int(first.day))),
                first_negative_period=int(first.period),
                last_negative_date=str(date(2025, 4, 1) + pd.Timedelta(days=int(last.day))),
                last_negative_period=int(last.period),
            )
        )
    ranking.sort(key=lambda r: -r["negative_cells"])
    write_csv(reports / "reference_gap_negative_equipment_20261016.csv", ranking)
    fixed = pa.array(q["fixed_asset_keys"])
    fixed_values = []
    positive_values = []
    zero_output = 0
    for b in pq.ParquetFile(q["output_path"]).iter_batches(
        columns=["asset_key_candidate", "generation_avg_kw", "capacity_gap_reference_kw"]
    ):
        ids, gen, gap = [b.column(i) for i in range(3)]
        v = numeric(gap)
        p = numeric(gen)
        zero_output += int(((p == 0) & np.isfinite(v)).sum())
        positive_values.append(v[(p > 0) & np.isfinite(v)])
        fixed_values.append(v[pc.is_in(ids, value_set=fixed).to_numpy(zero_copy_only=False)])
    conditional = [
        dict(subset="固定通年完全観測363設備", unit="kW", **stats(np.concatenate(fixed_values))),
        dict(
            subset="正の平均発電出力セルのみ・診断条件別",
            unit="kW",
            **stats(np.concatenate(positive_values)),
        ),
    ]
    write_csv(reports / "reference_gap_conditional_distribution_20261016.csv", conditional)
    q.update(
        negative_asset_count=len(ranking),
        zero_output_calculable_cells=zero_output,
        zero_output_share_of_calculable=zero_output / q["calculable_cells"],
        top4_negative_equipment_share=sum(r["negative_cells"] for r in ranking[:4])
        / q["negative_gap_cells"],
        selected_reference_capacity_row_sum_kw=str(
            sum(
                (
                    Decimal(r["capacity_reference_raw_kw"])
                    for r in mapping
                    if r["research_use_status"] == "use_diagnostic"
                ),
                Decimal(0),
            )
        ),
        fixed_reference_capacity_row_sum_kw=str(
            sum(
                (
                    Decimal(r["capacity_reference_raw_kw"])
                    for r in mapping
                    if r["asset_key_candidate"] in q["fixed_asset_keys"]
                ),
                Decimal(0),
            )
        ),
        conditional_distributions=conditional,
    )
    (reports / "reference_capacity_gap_qa_20261016.json").write_text(
        json.dumps(q, ensure_ascii=False, indent=2) + "\n"
    )
    return q
