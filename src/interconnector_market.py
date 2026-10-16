"""既存OCCTO連系線の原セル付きETL。ネットワーク文脈を時刻キーで接続する。"""

import csv
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.raw_acquisition import detect_encoding
from src.time_normalization import (
    JST,
    parse_date,
    period_from_label,
    select_records,
    sha256,
    time_key,
)

VARIANTS = (
    "system_capacity_dayahead2_planned",
    "reserve_interconnector_nextday2",
    "reserve_interconnector_latest",
)
SYSTEM_MW = {
    "空容量": "available_capacity_mw",
    "計画潮流": "planned_flow_mw",
    "広域調整枠": "wide_area_adjustment_mw",
    "マージン": "margin_mw",
    "運用容量": "operating_capacity_mw",
    "運用容量拡大分空容量": "expanded_available_capacity_mw",
    "運用容量拡大分計画潮流": "expanded_planned_flow_mw",
    "運用容量拡大分運用容量": "expanded_operating_capacity_mw",
    "送電ＮＧ情報延べ容量": "transmission_ng_capacity_mw",
}
RESERVE_NAMES = {
    "運用容量": "operating_capacity",
    "マージン": "margin",
    "予想潮流": "forecast_flow",
    "計画潮流": "planned_flow",
    "空容量": "available_capacity",
    "移動後空容量": "after_transfer_capacity",
}


def numeric_columns(header, variant):
    if variant == VARIANTS[0]:
        if not set(SYSTEM_MW) <= set(header):
            raise ValueError("系統空容量の必須列がない")
        return SYSTEM_MW
    result = {"移動供給力(MW)": "transfer_supply_mw"}
    for raw in header:
        for jp, en in (("順方向", "forward"), ("逆方向", "reverse")):
            for word, name in RESERVE_NAMES.items():
                if raw == f"{jp}{word}(MW)":
                    result[raw] = en + "_" + name + "_mw"
    return result


def parse_quantity(value):
    if value in ("", "***"):
        return None, "blank" if value == "" else "official_suppressed_marker"
    try:
        d = Decimal(value)
        if not d.is_finite() or d.as_tuple().exponent < -6:
            raise InvalidOperation
        return d, None
    except InvalidOperation as exc:
        raise ValueError(f"未知の非数値/精度超過: {value!r}") from exc


def parse_update(value):
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M UPDATE"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=JST)
        except ValueError:
            continue
    raise ValueError(f"更新日時の書式が未対応: {value!r}")


def normalize_rows(raw_rows, record, preamble):
    system = record["variant"] == VARIANTS[0]
    header = list(raw_rows[0])
    numeric = numeric_columns(header, record["variant"])
    frames, missing = [], Counter()
    for index, raw in enumerate(raw_rows):
        d = parse_date(raw["年月日" if system else "対象年月日"])
        p = period_from_label(raw["時刻"], reference="end")
        if system:
            if raw["対象断面"] != "翌々日" or raw["策定／更新後"] != "策定":
                raise ValueError("未承認の断面/更新状態")
            if datetime.strptime(raw["策定日"], "%Y%m%d").date() != d - timedelta(days=2):
                raise ValueError("策定日が翌々日の条件に一致しない")
            if raw["方向"] not in ("順方向", "逆方向"):
                raise ValueError("未知の方向")
        row_number = index + (2 if system else 3)
        row = dict(raw)
        row.update(
            zip(
                ["delivery_date", "period", "timestamp_start", "timestamp_end", "timezone"],
                time_key(d, p),
                strict=True,
            )
        )
        row["delivery_date"] = d
        row["timestamp_start"] = datetime.fromisoformat(row["timestamp_start"]).astimezone(JST)
        row["timestamp_end"] = datetime.fromisoformat(row["timestamp_end"]).astimezone(JST)
        row.update(
            source_file=record["local_path"],
            source_sha256=record["sha256"],
            source_row=row_number,
            source_record_id=record["sha256"] + ":" + str(row_number),
            source_variant=record["variant"],
            source_update_header_raw=preamble,
            source_acquired_at=record.get("downloaded_at") or record.get("acquired_at"),
            link_name_raw=raw["連系線" if system else "連系線名"],
            direction_raw=raw["方向"] if system else "both_columns",
            directional_geography_status="not_assigned_no_import_aggregation",
            flow_semantics="planned"
            if system or record["variant"].endswith("latest")
            else "raw_forecast_label_manual_planned_label_unresolved",
        )
        update = parse_update(raw["最新更新年月日時刻"] if system else preamble)
        row["row_updated_at"] = update if system else None
        row["file_latest_updated_at"] = None if system else update
        row["initial_publication_datetime_known"] = False
        row["qa_update_after_delivery_start"] = update > row["timestamp_start"]
        row["qa_numeric_missing_columns"] = []
        for old, new in numeric.items():
            value, reason = parse_quantity(raw[old])
            row[new] = value
            if reason:
                row["qa_numeric_missing_columns"].append(old + ":" + reason)
                missing[old + ":" + reason] += 1
        frames.append(row)
    return frames, numeric, missing


def make_table(rows, numeric, schema=None):
    if schema is None:
        table = pa.Table.from_pylist(rows)
        for col in numeric.values():
            i = table.schema.get_field_index(col)
            table = table.set_column(i, col, pa.array([r[col] for r in rows], pa.decimal128(38, 6)))
        for col in ("row_updated_at", "file_latest_updated_at"):
            i = table.schema.get_field_index(col)
            table = table.set_column(
                i, col, pa.array([r[col] for r in rows], pa.timestamp("us", "Asia/Tokyo"))
            )
        i = table.schema.get_field_index("qa_numeric_missing_columns")
        table = table.set_column(
            i,
            "qa_numeric_missing_columns",
            pa.array([r["qa_numeric_missing_columns"] for r in rows], pa.list_(pa.string())),
        )
        return table.replace_schema_metadata(
            {
                b"quantity_unit": b"MW",
                b"time_label": b"interval_end",
                b"scope": b"network records; no regional sum/congestion inference",
            }
        )
    return pa.Table.from_pylist(rows, schema=schema)


def build_network_contexts(root, output):
    output.mkdir(parents=True, exist_ok=False)
    records = [
        json.loads(line)
        for line in (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    selected = select_records(records, 2025)
    contexts = defaultdict(lambda: {v: [] for v in VARIANTS})
    qa = {}
    for variant in VARIANTS:
        files = [r for r in selected if r["variant"] == variant]
        writer, schema = None, None
        keys, ids, days, per_day_links = set(), set(), set(), defaultdict(set)
        groups = defaultdict(set)
        counts, missing, negatives = Counter(), Counter(), Counter()
        lineage, raw_columns = [], None
        path = output / (variant + "_FY2025.parquet")
        for record in files:
            source = root / record["local_path"]
            if sha256(source) != record["sha256"]:
                raise ValueError("raw SHA不一致")
            text, encoding = detect_encoding(source.read_bytes())
            all_rows = list(csv.reader(io.StringIO(text, newline="")))
            offset = 0 if variant == VARIANTS[0] else 1
            preamble = "" if offset == 0 else all_rows[0][0]
            header = all_rows[offset]
            if raw_columns is not None and raw_columns != header:
                raise ValueError("月/週間で原ヘッダーが変化")
            raw_columns = header
            if any(len(row) != len(header) for row in all_rows[offset + 1 :]):
                raise ValueError("原列数に不一致。無名列を黙って落とさない")
            raw_rows = [dict(zip(header, row, strict=True)) for row in all_rows[offset + 1 :]]
            rows, numeric, miss = normalize_rows(raw_rows, record, preamble)
            missing.update(miss)
            table = make_table(rows, numeric, schema)
            if writer is None:
                schema = table.schema
                writer = pq.ParquetWriter(path, schema, compression="zstd")
            writer.write_table(table)
            # 原セルと正規化値の独立比較は後段でParquetを再読込して実施。
            lineage.append(
                dict(
                    path=record["local_path"],
                    sha256=record["sha256"],
                    encoding=encoding,
                    rows=len(rows),
                    preamble=preamble,
                    original_filename=record.get("original_filename"),
                    acquired_at=record.get("downloaded_at"),
                    url=record.get("source_url"),
                )
            )
            for row in rows:
                t = (row["delivery_date"], row["period"])
                key = (*t, row["link_name_raw"], row["direction_raw"])
                if key in keys or row["source_record_id"] in ids:
                    raise ValueError("連系線キー/原行ID重複")
                keys.add(key)
                ids.add(row["source_record_id"])
                groups[(row["delivery_date"], row["link_name_raw"], row["direction_raw"])].add(
                    row["period"]
                )
                days.add(row["delivery_date"])
                per_day_links[str(row["delivery_date"])].add(row["link_name_raw"])
                contexts[t][variant].append(row["source_record_id"])
                counts["rows"] += 1
                counts["updated_after_delivery_start"] += row["qa_update_after_delivery_start"]
                for col in numeric.values():
                    if row[col] is not None and row[col] < 0:
                        negatives[col] += 1
        if writer is None:
            raise ValueError("連系線入力なし")
        writer.close()
        if len(days) != 365 or any(v != set(range(1, 49)) for v in groups.values()):
            raise ValueError("連系線の日付/48コマ欠落")
        # ファイル別フィルタで元ヘッダー・全元セル・追加数値を独立再照合。
        for source in lineage:
            loaded = pq.read_table(path, filters=[("source_file", "=", source["path"])]).to_pandas()
            text, _ = detect_encoding((root / source["path"]).read_bytes())
            original = list(csv.reader(io.StringIO(text, newline="")))[
                (1 if variant == VARIANTS[0] else 2) :
            ]
            if loaded[raw_columns].values.tolist() != original:
                raise ValueError("連系線の元セル不一致")
            for raw, normalized in numeric_columns(raw_columns, variant).items():
                expected = [parse_quantity(v)[0] for v in loaded[raw]]
                if loaded[normalized].tolist() != expected:
                    raise ValueError("連系線の数値書戻し不一致")
            if sha256(root / source["path"]) != source["sha256"]:
                raise ValueError("連系線raw変更")
        qa[variant] = dict(
            files=len(files),
            days=len(days),
            **counts,
            duplicate_keys=0,
            parse_failures=0,
            missing_numeric=dict(missing),
            negative_values=dict(negatives),
            links_by_date={k: sorted(v) for k, v in sorted(per_day_links.items())},
            raw_columns=raw_columns,
            normalized_numeric_columns=numeric,
            source_files=lineage,
            all_source_cells_preserved=True,
            initial_publication_datetime_known=False,
            geography_assigned=False,
        )
    rows = []
    for (d, p), values in sorted(contexts.items()):
        row = dict(delivery_date=d, period=p, network_context_id=f"{d}:{p:02d}")
        for variant in VARIANTS:
            if not values[variant]:
                raise ValueError("ネットワーク時刻でソース欠落")
            row[variant + "__source_record_ids"] = values[variant]
            row[variant + "__record_count"] = len(values[variant])
        rows.append(row)
    if len(rows) != 17520:
        raise ValueError("ネットワーク時刻数不正")
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, output / "network_time_contexts_FY2025.parquet", compression="zstd")
    if not pq.read_table(output / "network_time_contexts_FY2025.parquet").equals(table):
        raise ValueError("ネットワーク参照の書戻し不一致")
    return pd.DataFrame(rows), qa
