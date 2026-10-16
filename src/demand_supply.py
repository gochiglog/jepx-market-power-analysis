"""需要を元頻度で、予備率を計画断面別に整備する。補間・公表値置換なし。"""

import csv
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.interconnector_market import parse_quantity, parse_update
from src.market_panel import AREAS, MARKET_KEY, checked_join, frozen_files, key_qa
from src.raw_acquisition import detect_encoding
from src.time_normalization import (
    JST,
    TIME_COLUMNS,
    demand_key,
    fiscal_dates,
    parse_date,
    period_from_label,
    select_records,
    sha256,
    time_key,
)

OLD_MARKET = "data/processed/market/20261016_market_diagnostic_v1"
RESERVE_NUMERIC = {
    "広域ブロック需要(MW)": "block_demand_forecast_mw",
    "広域ブロック供給力(MW)": "block_supply_plan_mw",
    "広域ブロック予備力(MW)": "block_reserve_published_mw",
    "広域予備率(%)": "wide_area_reserve_rate_published_pct",
    "広域使用率(%)": "wide_area_usage_rate_published_pct",
    "エリア需要(MW)": "area_demand_forecast_mw",
    "エリア供給力(MW)": "area_supply_plan_mw",
    "エリア予備力(MW)": "area_reserve_published_mw",
}
DEMAND_TYPES = {
    "hourly": ("demand_hourly_csv_mwh", "MWh"),
    "daily_energy": ("demand_daily_energy_mwh", "MWh"),
    "daily_peak": ("demand_daily_peak_mw", "MW"),
}


def raw_csv(root, record, preamble_count):
    path = root / record["local_path"]
    if sha256(path) != record["sha256"]:
        raise ValueError("入力raw SHA不一致")
    text, encoding = detect_encoding(path.read_bytes())
    cells = list(csv.reader(io.StringIO(text, newline="")))
    header = cells[preamble_count]
    if len(set(header)) != len(header) or any(
        len(r) != len(header) for r in cells[preamble_count + 1 :]
    ):
        raise ValueError("元ヘッダー/列数不整合。末尾列を黙って落とさない")
    rows = [dict(zip(header, r, strict=True)) for r in cells[preamble_count + 1 :]]
    provenance = dict(
        path=record["local_path"],
        sha256=record["sha256"],
        original_filename=record.get("original_filename"),
        downloaded_at=record.get("downloaded_at"),
        source_url=record.get("source_url"),
        encoding=encoding,
        original_header=header,
        data_rows=len(rows),
    )
    preamble = cells[0][0] if preamble_count else ""
    return rows, preamble, provenance


def provenance_columns(record, row):
    return dict(
        source_file=record["local_path"],
        source_sha256=record["sha256"],
        source_row=row,
        source_record_id=record["sha256"] + ":" + str(row),
        source_downloaded_at=record.get("downloaded_at"),
        initial_publication_datetime_known=False,
    )


def normalize_demand_row(raw, record, row_number):
    d = parse_date(raw["年月日"])
    label = raw["時間帯"]
    if label == "日電力量(MWh)":
        kind = "daily_energy"
    elif label == "日最大電力(MW)":
        kind = "daily_peak"
    else:
        kind = "hourly"
    time_values, granularity = demand_key(d, label)
    if kind == "hourly" and granularity != "hourly":
        raise ValueError("需要頻度不整合")
    area_columns = [*AREAS, "沖縄", "１０エリア計"]
    if set(raw) != {"年月日", "時間帯", *area_columns}:
        raise ValueError("未知の需要列。元列を黙って落とさない")
    out = []
    for area in area_columns:
        value, reason = parse_quantity(raw[area])
        row = dict(raw)
        quantity, unit = DEMAND_TYPES[kind]
        row.update(
            provenance_columns(record, row_number),
            area=area,
            delivery_date=d,
            area_scope="ten_area_total" if area == "１０エリア計" else "individual_area",
            time_granularity=kind,
            quantity_unit=unit,
            demand_information_stage="reported_actual_csv",
            qa_source_value_missing=reason is not None,
            source_missing_reason=reason,
            source_area_column=area,
            qa_hourly_unit_display_conflict=kind == "hourly",
        )
        row[quantity] = value
        if kind == "hourly":
            row["hour_of_delivery"] = int(label[:2])
            row["timestamp_start"] = datetime.fromisoformat(time_values[2]).astimezone(JST)
            row["timestamp_end"] = datetime.fromisoformat(time_values[3]).astimezone(JST)
            row["timezone"] = "Asia/Tokyo"
        out.append(row)
    return kind, out


def numeric_parquet(frame, path, quantity_columns):
    table = pa.Table.from_pandas(frame, preserve_index=False)
    for col in quantity_columns:
        index = table.schema.get_field_index(col)
        table = table.set_column(index, col, pa.array(frame[col].tolist(), pa.decimal128(38, 6)))
    for field in list(table.schema):
        if pa.types.is_list(field.type):
            index = table.schema.get_field_index(field.name)
            table = table.set_column(
                index,
                field.name,
                pa.array(frame[field.name].tolist(), pa.list_(pa.field("element", pa.string()))),
            )
    pq.write_table(table, path, compression="zstd")
    if not table.equals(pq.read_table(path), check_metadata=True):
        raise ValueError("Parquet再読込不一致")
    return dict(
        path=str(path),
        rows=len(frame),
        columns=len(frame.columns),
        sha256=sha256(path),
        nulls={c: int(frame[c].isna().sum()) for c in frame},
        numeric_parse_failures=0,
        quantity_nulls={c: int(frame[c].isna().sum()) for c in quantity_columns},
        quantity_negative={c: int(frame[c].lt(0).sum()) for c in quantity_columns},
    )


def demand_etl(root, selected, output):
    buffers = {k: [] for k in DEMAND_TYPES}
    files = []
    for record in selected:
        if record["variant"] != "demand":
            continue
        rows, _, provenance = raw_csv(root, record, 0)
        files.append(provenance)
        for i, raw in enumerate(rows, 2):
            kind, normalized = normalize_demand_row(raw, record, i)
            buffers[kind].extend(normalized)
    frames = {k: pd.DataFrame(v) for k, v in buffers.items()}
    qa = dict(
        source_files=files,
        source_rows=sum(f["data_rows"] for f in files),
        hourly_csv_unit="MWh per CSV table 13-6",
        screen_definition_conflict="画面表13-5は1時間ごとの最大電力MW。自動換算しない",
        interpolated=False,
        join_to_half_hour_market=False,
        original_columns_preserved=True,
    )
    for kind, frame in frames.items():
        keys = ["area", "delivery_date"] + (["hour_of_delivery"] if kind == "hourly" else [])
        expected = 365 * 11 * (24 if kind == "hourly" else 1)
        key_qa(frame, keys, expected=expected)
        if set(frame.delivery_date) != set(fiscal_dates(2025)):
            raise ValueError("需要の対象日欠落")
        if (
            kind == "hourly"
            and not frame.groupby(["area", "delivery_date"])
            .hour_of_delivery.apply(lambda x: set(x) == set(range(24)))
            .all()
        ):
            raise ValueError("需要の日内24時間が不完全")
        qa[kind] = numeric_parquet(
            frame, output / f"occto_demand_{kind}_FY2025.parquet", [DEMAND_TYPES[kind][0]]
        )
        qa[kind].update(duplicate_keys=0, days=365, individual_areas=10, aggregate_areas=1)
    # 各原行の全wideセルを1回だけ取り出して再照合し、11エリア展開との対応を監査。
    for source in files:
        subsets = [
            f[f.source_file.eq(source["path"])].drop_duplicates("source_record_id")
            for f in frames.values()
        ]
        back = pd.concat(subsets).sort_values("source_row")
        text, _ = detect_encoding((root / source["path"]).read_bytes())
        original = list(csv.reader(io.StringIO(text, newline="")))[1:]
        if back[source["original_header"]].values.tolist() != original:
            raise ValueError("需要の原行/原セル不一致")
    # 数値的整合診断に限定。日最大と時間行を物理的に同一定義とは確定しない。
    hours = frames["hourly"]
    g = hours.groupby(["area", "delivery_date"], observed=True).demand_hourly_csv_mwh
    sums = g.sum(min_count=24).rename("hourly_csv_sum_mwh")
    maxs = g.max().rename("hourly_csv_max_raw_number")
    checks = frames["daily_energy"][["area", "delivery_date", "demand_daily_energy_mwh"]].merge(
        sums.reset_index(), on=["area", "delivery_date"], how="left", validate="one_to_one"
    )
    checks["hour_sum_minus_day_energy_mwh"] = (
        checks.hourly_csv_sum_mwh - checks.demand_daily_energy_mwh
    )
    peaks = frames["daily_peak"][["area", "delivery_date", "demand_daily_peak_mw"]].merge(
        maxs.reset_index(), on=["area", "delivery_date"], how="left", validate="one_to_one"
    )
    checks = checks.merge(peaks, on=["area", "delivery_date"], validate="one_to_one")
    checks["hourly_number_max_equals_daily_peak_number"] = (
        checks.hourly_csv_max_raw_number == checks.demand_daily_peak_mw
    )
    qa["daily_energy_numeric_matches"] = int(checks.hour_sum_minus_day_energy_mwh.eq(0).sum())
    qa["daily_energy_uncomparable"] = int(checks.hour_sum_minus_day_energy_mwh.isna().sum())
    qa["daily_energy_numeric_mismatches"] = (
        int(checks.hour_sum_minus_day_energy_mwh.notna().sum()) - qa["daily_energy_numeric_matches"]
    )
    qa["daily_comparisons"] = len(checks)
    return frames, qa, checks


def normalize_reserve_row(raw, record, row_number, preamble):
    d = parse_date(raw["対象年月日"])
    p = period_from_label(raw["時刻"], reference="end")
    row = dict(raw)
    row.update(provenance_columns(record, row_number))
    row.update(zip(TIME_COLUMNS, time_key(d, p), strict=True))
    row.update(
        delivery_date=d,
        area=raw["エリア名"],
        source_variant=record["variant"],
        block_number_raw=raw["ブロックNo"],
        file_update_header_raw=preamble,
        file_latest_updated_at=parse_update(preamble),
        reserve_information_stage="dayahead2_plan"
        if record["variant"] == "reserve_nextday2"
        else "dayahead_intraday_plan_latest_snapshot",
        reserve_values_are_realized_actuals=False,
        source_missing_numeric_reasons=[],
    )
    for c in ("timestamp_start", "timestamp_end"):
        row[c] = datetime.fromisoformat(row[c]).astimezone(JST)
    row["qa_file_update_after_target_start"] = (
        row["file_latest_updated_at"] > row["timestamp_start"]
    )
    for source, target in RESERVE_NUMERIC.items():
        value, reason = parse_quantity(raw[source])
        row[target] = value
        if reason:
            row["source_missing_numeric_reasons"].append(source + ":" + reason)
    return row


def reserve_etl(root, selected, output):
    frames, qa = {}, {}
    for variant in ("reserve_nextday2", "reserve_latest"):
        rows, files = [], []
        for record in selected:
            if record["variant"] != variant:
                continue
            originals, preamble, provenance = raw_csv(root, record, 1)
            files.append(provenance)
            rows.extend(
                normalize_reserve_row(raw, record, i, preamble)
                for i, raw in enumerate(originals, 3)
            )
        f = pd.DataFrame(rows)
        key_qa(f, MARKET_KEY, expected=175200)
        if (
            set(f.delivery_date) != set(fiscal_dates(2025))
            or not f.groupby(["area", "delivery_date"])
            .period.apply(lambda p: set(p) == set(range(1, 49)))
            .all()
        ):
            raise ValueError("予備率の年度/48コマ欠落")
        frames[variant] = f
        q = numeric_parquet(f, output / f"occto_{variant}_FY2025.parquet", RESERVE_NUMERIC.values())
        q.update(
            source_files=files,
            duplicate_keys=0,
            days=365,
            file_update_after_target_start_rows=int(f.qa_file_update_after_target_start.sum()),
            missing_markers=dict(
                Counter(r for reasons in f.source_missing_numeric_reasons for r in reasons)
            ),
            published_rate_recalculated=False,
            rate_recalculation_hold_reason="当該CSVの全構成項目・調整項と公式式の対応は未確定。公表値を保持",
            original_columns_preserved=True,
        )
        shared = [v for k, v in RESERVE_NUMERIC.items() if k.startswith("広域")]
        conflicts = f.groupby(["delivery_date", "period", "block_number_raw"])[shared].nunique(
            dropna=False
        )
        q["block_shared_value_conflict_groups"] = int(conflicts.gt(1).any(axis=1).sum())
        q["block_values_repeated_by_member_area"] = True
        for source in files:
            loaded = pq.read_table(
                output / f"occto_{variant}_FY2025.parquet",
                filters=[("source_file", "=", source["path"])],
            ).to_pandas()
            text, _ = detect_encoding((root / source["path"]).read_bytes())
            original = list(csv.reader(io.StringIO(text, newline="")))[2:]
            if loaded[source["original_header"]].values.tolist() != original:
                raise ValueError("予備率の原セル不一致")
            for raw, normalized in RESERVE_NUMERIC.items():
                if loaded[normalized].tolist() != [parse_quantity(v)[0] for v in loaded[raw]]:
                    raise ValueError("予備率数値の再照合不一致")
        qa[variant] = q
    return frames, qa


def frozen_inputs(root, repo):
    files = frozen_files(root, repo)
    manifest_path = repo / "metadata/market_panel_FY2025_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    files.append(dict(path=str(manifest_path), sha256=sha256(manifest_path)))
    for row in manifest["output_files"]:
        p = root / row["path"]
        if sha256(p) != row["sha256"]:
            raise ValueError("既存市場固定版SHA不一致")
        files.append(dict(path=str(p), sha256=row["sha256"]))
    return files


def build_connected_market(root, frames, output):
    original_paths = [root / OLD_MARKET / "market_base_FY2025.parquet"]
    original_paths += sorted((root / OLD_MARKET).glob("market_hjks_outage_*_FY2025.parquet"))
    qa = []
    for source in original_paths:
        base = pq.read_table(source).to_pandas()
        before = base.copy()
        joins = []
        for variant, f in frames.items():
            right = f.rename(columns={c: variant + "__" + c for c in f if c not in MARKET_KEY})
            base, join, unused = checked_join(base, right, MARKET_KEY, label=variant)
            if len(unused) != 17520 or set(unused.area) != {"沖縄"}:
                raise ValueError("予備率の対象範囲差が想定外")
            for col in ("timestamp_start", "timestamp_end", "timezone"):
                if not (base[col] == base[variant + "__" + col]).all():
                    raise ValueError("市場/予備率の時間契約不一致")
            joins.append(join)
        pd.testing.assert_frame_equal(base[before.columns], before)
        q = numeric_parquet(
            base, output / source.name.replace(".parquet", "_with_reserve.parquet"), []
        )
        q.update(source=str(source), joins=joins, base_all_cells_unchanged=True)
        qa.append(q)
    return qa


def run(root: Path, repo: Path, output: Path):
    if output.exists():
        raise ValueError("出力先が既存。固定版を上書きしない")
    frozen = frozen_inputs(root, repo)
    records = [
        json.loads(line)
        for line in (repo / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
    ]
    selected = select_records(records, 2025)
    output.mkdir(parents=True)
    _, demand_qa, checks = demand_etl(root, selected, output)
    checks.to_csv(repo / "reports/demand_daily_consistency_20261016.csv", index=False)
    frames, reserve_qa = reserve_etl(root, selected, output)
    market_qa = build_connected_market(root, frames, output)
    from src.interconnector_directions import annotate_existing_links

    direction_qa = annotate_existing_links(root, repo, output)
    for row in frozen:
        if sha256(Path(row["path"])) != row["sha256"]:
            raise ValueError("既存固定版が変更された")
    # 全raw登録203件＋既存参考39件も前後の原SHAに照合。
    raw_checked = 0
    for r in records:
        if r["status"] not in ("downloaded", "imported"):
            continue
        p = root / r["local_path"]
        if sha256(p) != r["sha256"] or p.stat().st_size != r["file_size"]:
            raise ValueError("raw変更")
        raw_checked += 1
    refs = json.loads(
        (repo / "metadata/research/asset_reference_manifest_20261016.json").read_text()
    )
    for r in refs:
        if sha256(root / r["local_path"]) != r["sha256"]:
            raise ValueError("参考原本変更")
    qa = dict(
        version="20261016_demand_supply_direction_v1",
        demand=demand_qa,
        reserve=reserve_qa,
        market_connections=market_qa,
        directions=direction_qa,
        frozen_inputs=frozen,
        frozen_inputs_unchanged=True,
        raw_sha_checked=raw_checked,
        reference_raw_sha_checked=len(refs),
        actual_demand_interpolated=False,
        baseline_selected=False,
        outputs=[
            dict(path=str(p.relative_to(root)), sha256=sha256(p), size=p.stat().st_size)
            for p in sorted(output.rglob("*.parquet"))
        ],
    )
    (repo / "reports/demand_supply_direction_qa_20261016.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    return qa
