"""発電実績long化とHJKS全版の非確定対応診断。補正・最終joinは行わない。"""

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.jepx_spot import parse_quantity
from src.raw_acquisition import detect_encoding
from src.time_normalization import JST, fiscal_dates, parse_date, sha256, time_key

DECIMAL = pa.decimal128(38, 6)
HYPHENS = str.maketrans({c: "-" for c in "‐‑‒–—―−﹘﹣－"})


def normalize_name(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).translate(HYPHENS)).strip()


def identity(row):
    return (row["発電所コード"], row["エリア"], row["発電所名"], row["ユニット名"])


def asset_id(row):
    return hashlib.sha256(json.dumps(identity(row), ensure_ascii=False).encode()).hexdigest()


def read_raw(root, record):
    path = root / record["local_path"]
    if sha256(path) != record["sha256"]:
        raise ValueError(f"raw SHA不一致: {path}")
    text, encoding = detect_encoding(path.read_bytes())
    reader = csv.reader(io.StringIO(text, newline=""))
    headers = next(reader)
    if len(set(headers)) != len(headers):
        raise ValueError("元列名重複")
    rows = []
    for number, cells in enumerate(reader, 2):
        if len(cells) not in (len(headers), len(headers) + 1) or (
            len(cells) > len(headers) and cells[-1] != ""
        ):
            raise ValueError(f"未対応列数: {path}:{number}")
        rows.append((number, dict(zip(headers, cells)), cells[len(headers) :]))
    return headers, rows, encoding


def attributes(row):
    return {
        "area": row["エリア"],
        "plant_code": row["発電所コード"],
        "plant_name": row["発電所名"],
        "unit_name": row["ユニット名"],
        "plant_name_raw": row["発電所名"],
        "plant_name_normalized": normalize_name(row["発電所名"]),
        "unit_name_raw": row["ユニット名"],
        "unit_name_normalized": normalize_name(row["ユニット名"]),
        "asset_key_candidate": asset_id(row),
    }


def safe_numeric(value, issues, location):
    try:
        number = parse_quantity(value)
        if number is not None and max(0, -number.as_tuple().exponent) > 6:
            raise ValueError("小数6桁超。丸めず要確認")
        return number
    except ValueError:
        issues.append({**location, "original_value": value})
        return None


def date_value(value, issues, location):
    if not value:
        return None
    try:
        return date.fromisoformat(value.replace("/", "-"))
    except ValueError:
        issues.append({**location, "original_value": value})
        return None


def datetime_value(value, issues, location):
    if not value:
        return None
    try:
        if not re.fullmatch(r"\d{4}[/-]\d{2}[/-]\d{2} \d{2}:\d{2}(?::\d{2})?", value):
            raise ValueError("日時の時刻部分がない、または未対応表記。時刻を補わない")
        return datetime.fromisoformat(value.replace("/", "-")).replace(tzinfo=JST)
    except ValueError:
        issues.append({**location, "original_value": value})
        return None


def write_rows(path, rows, types=None):
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    for name, dtype in (types or {}).items():
        table = table.set_column(
            table.schema.get_field_index(name), name, pa.array([r[name] for r in rows], type=dtype)
        )
    pq.write_table(table, path, compression="zstd")
    if not pq.read_table(path).equals(table):
        raise ValueError("Parquet再読込不一致")


def generation_long(root, records, output):
    if output.exists():
        raise FileExistsError(output)
    expected = set(fiscal_dates(2025))
    day_keys, days, assets = set(), set(), {}
    qa = {
        "raw_day_rows": 0,
        "long_rows": 0,
        "null_count": 0,
        "negative_count": 0,
        "numeric_parse_failures": [],
        "duplicate_key_count": 0,
        "inputs": [],
    }
    raw_total = Decimal(0)
    raw_absolute = Decimal(0)
    writer = None
    output.parent.mkdir(parents=True, exist_ok=True)
    for record in records:
        headers, raw, encoding = read_raw(root, record)
        columns = [h for h in headers if re.fullmatch(r"\d\d:\d\d\[kWh\]", h)]
        expected_columns = [f"{p // 2:02d}:{(p % 2) * 30:02d}[kWh]" for p in range(1, 49)]
        if columns != expected_columns:
            raise ValueError("48個の終端時刻列と一致しない")
        nonperiod = [h for h in headers if h not in columns]
        qa["inputs"].append(
            {"path": record["local_path"], "sha256": record["sha256"], "encoding": encoding}
        )
        for begin in range(0, len(raw), 1024):
            rows = []
            for number, row, extra in raw[begin : begin + 1024]:
                day = parse_date(row["対象日"])
                aid = asset_id(row)
                key = (day, aid)
                if key in day_keys:
                    qa["duplicate_key_count"] += 48
                day_keys.add(key)
                days.add(day)
                stats = assets.setdefault(
                    aid,
                    {
                        **attributes(row),
                        "raw_day_rows": 0,
                        "long_rows": 0,
                        "generation_sum": Decimal(0),
                        "absolute_generation_sum": Decimal(0),
                        "observed_dates": set(),
                    },
                )
                stats["raw_day_rows"] += 1
                stats["observed_dates"].add(str(day))
                fixed = {
                    **{h: row[h] for h in nonperiod},
                    **attributes(row),
                    "source_file": record["local_path"],
                    "source_row": number,
                    "source_sha256": record["sha256"],
                    "source_extra_cells_json": json.dumps(extra),
                    "delivery_date": day,
                    "timezone": "Asia/Tokyo",
                }
                for period, column in enumerate(columns, 1):
                    value = safe_numeric(
                        row[column],
                        qa["numeric_parse_failures"],
                        {"file": record["local_path"], "row": number, "column": column},
                    )
                    qa["null_count"] += value is None
                    qa["negative_count"] += value is not None and value < 0
                    raw_total += value if value is not None else 0
                    raw_absolute += abs(value) if value is not None else 0
                    stats["generation_sum"] += value if value is not None else 0
                    stats["absolute_generation_sum"] += abs(value) if value is not None else 0
                    stats["long_rows"] += 1
                    times = time_key(day, period)
                    rows.append(
                        {
                            **fixed,
                            "period": period,
                            "timestamp_start": datetime.fromisoformat(times[2]),
                            "timestamp_end": datetime.fromisoformat(times[3]),
                            "generation_kwh_30min": value,
                            "generation_raw_value": row[column],
                            "source_period_column": column,
                        }
                    )
                qa["raw_day_rows"] += 1
            table = pa.Table.from_pylist(rows)
            table = table.set_column(
                table.schema.get_field_index("generation_kwh_30min"),
                "generation_kwh_30min",
                pa.array([r["generation_kwh_30min"] for r in rows], type=DECIMAL),
            )
            table = table.replace_schema_metadata(
                {
                    b"generation_unit": b"kWh/30min",
                    b"timezone": b"Asia/Tokyo",
                    b"scope": b"long only; no unit conversion or HJKS join",
                }
            )
            if writer is None:
                writer = pq.ParquetWriter(output, table.schema, compression="zstd")
            writer.write_table(table)
            qa["long_rows"] += len(rows)
        print(f"generation完了: {record['local_path']}", flush=True)
    if writer is None:
        raise ValueError("発電入力がない")
    writer.close()
    saved_total = Decimal(0)
    saved_nulls = saved_rows = 0
    for batch in pq.ParquetFile(output).iter_batches(columns=["generation_kwh_30min"]):
        values = batch.column(0).to_pylist()
        saved_total += sum((v for v in values if v is not None), Decimal(0))
        saved_nulls += sum(v is None for v in values)
        saved_rows += len(values)
    qa.update(
        days=len(days),
        missing_dates=list(map(str, sorted(expected - days))),
        unexpected_dates=list(map(str, sorted(days - expected))),
        raw_generation_sum=str(raw_total),
        long_generation_sum=str(saved_total),
        raw_absolute_sum=str(raw_absolute),
        raw_total_equals_long=raw_total == saved_total,
        roundtrip_rows=saved_rows,
        roundtrip_nulls=saved_nulls,
        output_path=str(output),
        output_sha256=sha256(output),
        keys="plant_code × area × 元plant_name × 元unit_name × delivery_date × period",
        null_policy="空セルはnull、ゼロ化・補間なし。発電量coverageの分母は数値観測分のみ。欠測量は不明",
    )
    qa["passed"] = (
        not (
            expected - days
            or days - expected
            or qa["duplicate_key_count"]
            or qa["numeric_parse_failures"]
        )
        and raw_total == saved_total
        and saved_rows == qa["long_rows"]
        and saved_nulls == qa["null_count"]
    )
    return assets, qa


def hjks_snapshot(root, record, directory):
    _, raw, encoding = read_raw(root, record)
    outage = record["source_id"] == "hjks_outage"
    result = []
    qa = {
        "rows": len(raw),
        "encoding": encoding,
        "numeric_parse_failures": [],
        "date_parse_failures": [],
        "source": record,
        "capacity_values": [],
        "negative_reduction_count": 0,
        "start_after_recovery_date": 0,
        "same_day_recovery_ambiguous": 0,
        "start_before_fy_recovery_in_or_after_fy": 0,
        "start_before_fy_recovery_unknown": 0,
    }
    candidate_keys = Counter()
    unit_keys = Counter()
    updates = defaultdict(set)
    for number, row, extra in raw:
        loc = {"row": number}
        entry = {
            **row,
            **attributes(row),
            "source_file": record["local_path"],
            "source_row": number,
            "source_sha256": record["sha256"],
            "source_extra_cells_json": json.dumps(extra),
            "snapshot_row_id": f"{record['sha256']}:{number}",
        }
        capacity = safe_numeric(row["認可出力"], qa["numeric_parse_failures"], loc)
        entry["authorized_capacity_kw"] = capacity
        if capacity is not None:
            qa["capacity_values"].append(capacity)
        unit_keys[(row["発電所コード"], row["エリア"], normalize_name(row["ユニット名"]))] += 1
        if outage:
            start = datetime_value(row["停止日時"], qa["date_parse_failures"], loc)
            recovery = date_value(row["復旧予定日"], qa["date_parse_failures"], loc)
            reduction = safe_numeric(row["低下量"], qa["numeric_parse_failures"], loc)
            entry.update(
                start_datetime=start,
                expected_recovery_date=recovery,
                expected_recovery_datetime=None,
                actual_recovery_datetime=None,
                reduction_kw=reduction,
                outage_type=row["停止区分"],
                publication_datetime=None,
                update_datetime=datetime_value(row["最終更新日時"], qa["date_parse_failures"], loc),
                unit=normalize_name(row["ユニット名"]),
                recovery_precision="day" if recovery else "unknown",
                event_id_verified=False,
            )
            qa["negative_reduction_count"] += reduction is not None and reduction < 0
            qa["start_after_recovery_date"] += bool(start and recovery and start.date() > recovery)
            qa["same_day_recovery_ambiguous"] += bool(
                start and recovery and start.date() == recovery
            )
            qa["start_before_fy_recovery_in_or_after_fy"] += bool(
                start
                and start.date() < date(2025, 4, 1)
                and recovery
                and recovery >= date(2025, 4, 1)
            )
            qa["start_before_fy_recovery_unknown"] += bool(
                start and start.date() < date(2025, 4, 1) and not recovery
            )
            key = (*identity(row), row["停止日時"], row["停止区分"], row["種別"])
            candidate_keys[key] += 1
            updates[key].add(row["最終更新日時"])
        else:
            entry.update(
                changed_authorized_capacity_kw=safe_numeric(
                    row["認可出力（変更後）"], qa["numeric_parse_failures"], loc
                ),
                capacity_effective_date=date_value(
                    row["適用開始日"], qa["date_parse_failures"], loc
                ),
                operation_start_date=date_value(row["稼働開始日"], qa["date_parse_failures"], loc),
                operation_end_date=date_value(row["稼働終了日"], qa["date_parse_failures"], loc),
                valid_from=None,
                valid_to=None,
            )
        result.append(entry)
    values = qa.pop("capacity_values")
    qa.update(
        plant_count=len({r["発電所コード"] for _, r, _ in raw}),
        plant_code_is_unique=len({r["発電所コード"] for _, r, _ in raw}) == len(raw),
        unit_key_count=len(unit_keys),
        duplicate_unit_candidate_groups=sum(n > 1 for n in unit_keys.values()),
        duplicate_unit_candidate_excess_rows=sum(n - 1 for n in unit_keys.values()),
        authorized_capacity_kw={
            "min": str(min(values)),
            "max": str(max(values)),
            "median_observation": str(sorted(values)[len(values) // 2]),
            "zero_count": sum(v == 0 for v in values),
            "negative_count": sum(v < 0 for v in values),
            "sum_snapshot_only": str(sum(values)),
        },
        candidate_event_duplicate_groups=sum(n > 1 for n in candidate_keys.values()),
        candidate_event_duplicate_excess_rows=sum(n - 1 for n in candidate_keys.values()),
        candidate_update_groups=sum(len(v) > 1 for v in updates.values()),
        field_ranges={
            column: {
                "min_raw": min(row[column] for _, row, _ in raw),
                "max_raw": max(row[column] for _, row, _ in raw),
                "blank_count": sum(row[column] == "" for _, row, _ in raw),
            }
            for column in (
                ("停止日時", "復旧予定日", "最終更新日時")
                if outage
                else ("稼働開始日", "稼働終了日", "適用開始日", "最終更新日時")
            )
        },
    )
    if not outage:
        qa["ended_before_fy_end"] = sum(
            row["operation_end_date"] is not None and row["operation_end_date"] < date(2026, 4, 1)
            for row in result
        )
        qa["operation_end_9999_count"] = sum(row["稼働終了日"] == "9999/12/31" for row in result)
    types = {"authorized_capacity_kw": DECIMAL}
    if outage:
        types.update(
            reduction_kw=DECIMAL,
            actual_recovery_datetime=pa.timestamp("us", tz="Asia/Tokyo"),
            expected_recovery_datetime=pa.timestamp("us", tz="Asia/Tokyo"),
            publication_datetime=pa.timestamp("us", tz="Asia/Tokyo"),
        )
    else:
        types.update(
            changed_authorized_capacity_kw=DECIMAL, valid_from=pa.date32(), valid_to=pa.date32()
        )
    if directory is not None:
        output = directory / f"{record['source_id']}_{len(result)}_{record['sha256'][:12]}.parquet"
        write_rows(output, result, types)
        qa["output_path"] = str(output)
    return result, qa


def mapping_candidates(assets, master):
    by_code = defaultdict(list)
    by_name = defaultdict(list)
    for row in master:
        by_code[(row["plant_code"], row["area"])].append(row)
        by_name[(row["area"], row["plant_name_normalized"])].append(row)
    candidates = []
    for aid, asset in assets.items():
        pool = by_code[(asset["plant_code"], asset["area"])]
        basis = "code_area" if pool else "area_normalized_plant_name"
        if not pool and asset["plant_name_normalized"]:
            pool = by_name[(asset["area"], asset["plant_name_normalized"])]
        plant_ids = {(r["plant_code"], r["area"]) for r in pool}
        same_name = [
            r for r in pool if r["plant_name_normalized"] == asset["plant_name_normalized"]
        ]
        if len(plant_ids) > 1 and same_name:
            pool = same_name
            plant_ids = {(r["plant_code"], r["area"]) for r in pool}
        unit_pool = [
            r
            for r in pool
            if asset["unit_name_normalized"]
            and r["unit_name_normalized"] == asset["unit_name_normalized"]
        ]
        candidates.append(
            {
                **asset,
                "observed_dates": json.dumps(sorted(asset["observed_dates"])),
                "asset_key_candidate": aid,
                "candidate_basis": basis,
                "plant_candidate_count": len(plant_ids),
                "unit_candidate_count": len(unit_pool),
                "plant_match": len(plant_ids) == 1,
                "unit_match": len(unit_pool) == 1,
                "plant_ambiguous": len(plant_ids) > 1,
                "unit_ambiguous": len(unit_pool) > 1,
                "candidate_snapshot_rows": "|".join(r["snapshot_row_id"] for r in unit_pool),
                "plant_candidate_snapshot_rows": "|".join(r["snapshot_row_id"] for r in pool),
                "final_mapping": False,
            }
        )
    summary = {}
    for level in ("unit", "plant"):
        matched = [r for r in candidates if r[f"{level}_match"]]
        total_volume = sum((r["generation_sum"] for r in candidates), Decimal(0))
        matched_volume = sum((r["generation_sum"] for r in matched), Decimal(0))
        absolute_total = sum((r["absolute_generation_sum"] for r in candidates), Decimal(0))
        absolute_matched = sum((r["absolute_generation_sum"] for r in matched), Decimal(0))
        total_rows = sum(r["long_rows"] for r in candidates)
        matched_rows = sum(r["long_rows"] for r in matched)
        summary[level] = {
            "row_match_count": matched_rows,
            "total_rows": total_rows,
            "row_match_rate": matched_rows / total_rows,
            "matched_asset_units": len(matched),
            "asset_unit_count": len(candidates),
            "unit_match_rate": len(matched) / len(candidates),
            "generation_kwh_matched": str(matched_volume),
            "generation_kwh_total_observed": str(total_volume),
            "generation_match_rate": str(matched_volume / total_volume) if total_volume else None,
            "absolute_generation_match_rate": str(absolute_matched / absolute_total)
            if absolute_total
            else None,
            "unmatched_unit_count": sum(not r[f"{level}_match"] for r in candidates),
            "ambiguity_asset_unit_count": sum(r[f"{level}_ambiguous"] for r in candidates),
        }
        plants = defaultdict(list)
        for row in candidates:
            plants[(row["plant_code"], row["area"], row["plant_name_normalized"])].append(row)
        all_match = sum(all(r[f"{level}_match"] for r in members) for members in plants.values())
        summary[level].update(
            plant_count=len(plants),
            fully_matched_plant_count=all_match,
            unmatched_plant_count=len(plants) - all_match,
            plant_match_rate=all_match / len(plants),
        )
        if level == "plant":
            plant_days = defaultdict(list)
            for row in candidates:
                for day in json.loads(row["observed_dates"]):
                    key = (row["plant_code"], row["area"], row["plant_name_normalized"], day)
                    plant_days[key].append(row["plant_match"])
            summary[level]["generation_unit_row_count"] = total_rows
            summary[level]["generation_unit_row_match_count"] = matched_rows
            summary[level]["total_rows"] = len(plant_days) * 48
            summary[level]["row_match_count"] = sum(all(v) for v in plant_days.values()) * 48
            summary[level]["row_match_rate"] = (
                summary[level]["row_match_count"] / summary[level]["total_rows"]
            )
            summary[level]["row_grain"] = (
                "observed plant × day × period; 値の集計・最終joinは未実施"
            )
    return candidates, summary


def compare_snapshots(root, snapshots, report_dir):
    original, versions, duplicates = {}, [], []
    for label, snapshot in snapshots.items():
        record = snapshot["source"]
        headers, rows, _ = read_raw(root, record)
        original[label] = {tuple(row[h] for h in headers) for _, row, _ in rows}
        ranges = snapshot["field_ranges"]
        versions.append(
            {
                "snapshot": label,
                "rows": len(rows),
                "source_path": record["local_path"],
                "original_filename": record["original_filename"],
                "downloaded_at": record.get("downloaded_at") or "未確認",
                "registered_at": record.get("registered_at") or "not_applicable",
                "search_parameters": json.dumps(
                    record.get("request_parameters"), ensure_ascii=False
                ),
                "conditions": record["notes"],
                "period_min_raw": ranges.get("停止日時", {}).get("min_raw", "台帳snapshot"),
                "period_max_raw": ranges.get("停止日時", {}).get("max_raw", "台帳snapshot"),
                "ended_before_fy_end": snapshot.get("ended_before_fy_end", "not_applicable"),
                "history_verified": "未確認。ファイル行数を履歴件数と同一視しない",
                "capacity_valid_from_blank": ranges.get("適用開始日", {}).get(
                    "blank_count", "not_applicable"
                ),
            }
        )
        if record["source_id"] == "hjks_outage":
            groups = defaultdict(list)
            for number, row, _ in rows:
                key = (*identity(row), row["停止日時"], row["停止区分"], row["種別"])
                groups[key].append((number, row["最終更新日時"]))
            for key, members in groups.items():
                if len(members) > 1:
                    duplicates.append(
                        {
                            "snapshot": label,
                            "plant_code": key[0],
                            "area": key[1],
                            "plant_name_raw": key[2],
                            "unit_name_raw": key[3],
                            "start_datetime_raw": key[4],
                            "outage_type": key[5],
                            "subtype": key[6],
                            "rows": len(members),
                            "distinct_update_times": len({t for _, t in members}),
                            "source_rows": "|".join(str(n) for n, _ in members),
                            "update_times_json": json.dumps(sorted({t for _, t in members})),
                            "event_identity_verified": False,
                        }
                    )
    for filename, data in (
        ("hjks_snapshot_comparison.csv", versions),
        ("hjks_outage_duplicate_update_candidates.csv", duplicates),
    ):
        if not data:
            continue
        with (report_dir / filename).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(data)
    result = {}
    for kind, left, right in (
        ("unit", "hjks_unit_529", "hjks_unit_677"),
        ("outage", "hjks_outage_58852", "hjks_outage_7828"),
    ):
        if left not in original or right not in original:
            continue
        a, b = original[left], original[right]
        result[kind] = {
            "left": left,
            "right": right,
            "identical_original_rows": len(a & b),
            "left_distinct_rows": len(a),
            "right_distinct_rows": len(b),
            "left_only_rows": len(a - b),
            "right_only_rows": len(b - a),
            "not_revision_history_proof": True,
        }
    return result


def run(root: Path, repo: Path):
    records = [
        json.loads(x)
        for x in (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
    ]
    selected = [
        r
        for r in records
        if r["source_id"] == "occto_generation"
        and r["status"] == "downloaded"
        and r.get("phase") == "full"
        and r.get("period_start", "") >= "2025-04-01"
        and r.get("period_end", "") <= "2026-03-31"
    ]
    selected.sort(key=lambda r: r["period_start"])
    if len(selected) != 12:
        raise ValueError("FY2025公式月別12件を確認できない")
    hjks = [
        r
        for r in records
        if r["source_id"] in ("hjks_unit", "hjks_outage")
        and r["status"] in ("downloaded", "imported")
    ]
    out = root / "data/interim"
    assets, generation_qa = generation_long(
        root, selected, out / "occto/generation/generation_long_FY2025.parquet"
    )
    report = {
        "generation": generation_qa,
        "snapshots": {},
        "mapping": {},
        "status": "pending_research_decisions",
        "baseline_selected": False,
        "headroom_calculated": False,
        "final_join_performed": False,
    }
    repo.joinpath("reports").mkdir(exist_ok=True)
    for record in hjks:
        rows, qa = hjks_snapshot(root, record, out / "hjks/diagnostics")
        label = f"{record['source_id']}_{len(rows)}"
        report["snapshots"][label] = qa
        if record["source_id"] == "hjks_unit":
            candidates, summary = mapping_candidates(assets, rows)
            report["mapping"][label] = summary
            p = repo / f"reports/generation_hjks_{len(rows)}_mapping_candidates.csv"
            with p.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(candidates[0]), lineterminator="\n")
                writer.writeheader()
                writer.writerows(candidates)
    report["cross_snapshot_comparison"] = compare_snapshots(
        root, report["snapshots"], repo / "reports"
    )
    report["raw_unchanged"] = all(
        sha256(root / r["local_path"]) == r["sha256"] for r in selected + hjks
    )
    p = repo / "reports/generation_hjks_FY2025_qa.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not generation_qa["passed"] or not report["raw_unchanged"]:
        raise ValueError(f"QA異常: {p}")
    return report
