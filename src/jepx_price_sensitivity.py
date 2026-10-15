"""原系列を保持し、承認済み用途分離に基づく価格感応度Parquetを作る。"""

import csv
import io
import json
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.jepx_spot import choose_canonical
from src.jepx_spot import transform as transform_spot
from src.price_sensitivity_audit import compare, inspect, select_canonical
from src.raw_acquisition import detect_encoding
from src.time_normalization import sha256, time_key

SCENARIO_COLUMNS = {
    f"virtual_price_{side}_{mw}mw": f"{jp}{mw}MW"
    for mw in (500, 1000, 5000)
    for side, jp in (("sell", "売"), ("buy", "買"))
}
DIFFERENCE_COLUMNS = {
    name.replace("virtual_price_", "published_difference_"): raw
    for name, raw in SCENARIO_COLUMNS.items()
}
DECISION_ID = "RD-008"


def original_rows(data):
    text, _ = detect_encoding(data)
    return list(csv.DictReader(io.StringIO(text, newline="")))


def decimal_array(values):
    scale = max([2] + [max(0, -v.as_tuple().exponent) for v in values if v is not None])
    if scale > 38:
        raise ValueError("原小数の精度がDecimal128を超える。丸めず停止")
    return pa.array(values, type=pa.decimal128(38, scale))


def transform(actual_bytes, difference_bytes, spot_bytes, hashes, year=2025):
    actual, actual_qa = inspect(actual_bytes, year)
    difference, diff_qa = inspect(difference_bytes, year)
    spot_table, spot_qa = transform_spot(spot_bytes, hashes["spot"], year)
    checks = {"actual": actual_qa, "difference": diff_qa}
    report = {
        "research_decision": DECISION_ID,
        "fiscal_year": year,
        "checks": checks,
        "spot_time_qa": spot_qa["time_qa"],
        "spot_numeric_parse_failures": spot_qa["numeric_parse_failures"],
        "spot_null_count": spot_qa["column_statistics"]["system_price"]["missing_count"],
        "all_passed": False,
        "processed_written": False,
        "status": "blocked_input_qa",
        "known_qa_issues": [],
        "price_baseline_difference_formula": "system_price_sensitivity_base - system_price_market",
        "difference_formula_officially_verified": False,
        "observed_internal_formula": "virtual_price - system_price_sensitivity_base",
        "official_inquiry_todo": (
            "価格感応度CSVの『実際のシステムプライス』とスポット市場CSVのシステムプライスが"
            "FY2025の一部コマで異なる理由をJEPXへ確認する。回答は進行条件にしない"
        ),
    }
    if (
        spot_table is None
        or report["spot_null_count"]
        or any(
            not check["time_qa"]["passed"] or check["numeric_parse_failures"] or check["nulls"]
            for check in checks.values()
        )
    ):
        return None, [], report
    spot = dict(
        zip(
            zip(
                map(str, spot_table["delivery_date"].to_pylist()),
                spot_table["period"].to_pylist(),
                strict=True,
            ),
            spot_table["system_price"].to_pylist(),
            strict=True,
        )
    )
    comparison = compare(actual, difference, spot)
    report["comparison"] = comparison
    if any(
        comparison["key_match"][name]
        for name in ("actual_unmatched", "difference_unmatched", "spot_unmatched")
    ):
        return None, [], report
    if comparison["candidate_formula_mismatches"]:
        report["status"] = "blocked_internal_difference_qa"
        return None, [], report

    raw_actual = original_rows(actual_bytes)
    raw_difference = original_rows(difference_bytes)
    # 順序を合わせるために行を捨てない。実数raw順を採用し差分rawの元行番号も保持する。
    keys = list(actual)
    difference_positions = {key: i for i, key in enumerate(difference)}
    dates, periods, starts, ends, baseline_qa = [], [], [], [], []
    for day, period in keys:
        market = spot[(day, period)]
        base = actual[(day, period)]["システムプライス"]
        timestamps = time_key(datetime.fromisoformat(day).date(), period)
        dates.append(datetime.fromisoformat(day).date())
        periods.append(period)
        starts.append(datetime.fromisoformat(timestamps[2]))
        ends.append(datetime.fromisoformat(timestamps[3]))
        baseline_qa.append(
            {
                "delivery_date": day,
                "period": period,
                "system_price_market": str(market),
                "system_price_sensitivity_base": str(base),
                "price_baseline_difference": str(base - market),
                "baseline_price_match_flag": base == market,
            }
        )
    arrays = {
        name: pa.array([row[name] for row in raw_actual], type=pa.string())
        for name in raw_actual[0]
    }
    arrays.update(
        {
            f"difference_raw__{name}": pa.array(
                [raw_difference[difference_positions[key]][name] for key in keys], type=pa.string()
            )
            for name in raw_difference[0]
        }
    )
    arrays.update(
        {
            "delivery_date": pa.array(dates, type=pa.date32()),
            "period": pa.array(periods, type=pa.int8()),
            "timestamp_start": pa.array(starts, type=pa.timestamp("us", tz="Asia/Tokyo")),
            "timestamp_end": pa.array(ends, type=pa.timestamp("us", tz="Asia/Tokyo")),
            "timezone": pa.array(["Asia/Tokyo"] * len(keys)),
            "source_actual_sha256": pa.array([hashes["sensitivity"]] * len(keys)),
            "source_difference_sha256": pa.array([hashes["sensitivity_diff"]] * len(keys)),
            "source_actual_row_number": pa.array(range(2, len(keys) + 2), type=pa.int32()),
            "source_difference_row_number": pa.array(
                [difference_positions[key] + 2 for key in keys], type=pa.int32()
            ),
            "system_price_sensitivity_base": decimal_array(
                [actual[key]["システムプライス"] for key in keys]
            ),
            **{
                name: decimal_array([actual[key][column] for key in keys])
                for name, column in SCENARIO_COLUMNS.items()
            },
            **{
                name: decimal_array([difference[key][column] for key in keys])
                for name, column in DIFFERENCE_COLUMNS.items()
            },
        }
    )
    units = {
        name: "JPY/kWh"
        for name in ("system_price_sensitivity_base", *SCENARIO_COLUMNS, *DIFFERENCE_COLUMNS)
    }
    table = pa.table(arrays).replace_schema_metadata(
        {
            b"research_units": json.dumps(units).encode(),
            b"timezone": b"Asia/Tokyo",
            b"research_decision": DECISION_ID.encode(),
            b"source_hashes": json.dumps(hashes).encode(),
            b"difference_reference": (
                b"sensitivity_base; published values retained, not recalculated"
            ),
            b"official_difference_sign_verified": b"false",
        }
    )
    report.update(
        status="completed_with_known_qa_issue"
        if comparison["spot_price_mismatch_count"]
        else "completed",
        all_passed=True,
        processed_rows=table.num_rows,
        processed_columns=table.column_names,
        numeric_schema={name: str(table.schema.field(name).type) for name in units},
        numeric_null_counts={name: table[name].null_count for name in units},
        original_column_mapping={
            "actual": {name: name for name in raw_actual[0]},
            "difference": {name: f"difference_raw__{name}" for name in raw_difference[0]},
        },
        price_units=units,
    )
    if comparison["spot_price_mismatch_count"]:
        report["known_qa_issues"].append(
            {
                "id": "system_price_baseline_mismatch",
                "count": comparison["spot_price_mismatch_count"],
                "cause": "未解決。公式仕様上は一致期待。独自修正なし",
                "blocking": False,
                "decision": DECISION_ID,
            }
        )
    return table, baseline_qa, report


def run(root: Path, repo: Path, year=2025):
    root, repo = root.resolve(), repo.resolve()
    output = root / f"data/processed/jepx/price_sensitivity/jepx_price_sensitivity_FY{year}.parquet"
    if output.exists():
        raise FileExistsError(f"既存processedへ上書きしない: {output}")
    if not output.resolve().is_relative_to(root / "data/processed"):
        raise ValueError("processed領域外への出力")
    records = [
        json.loads(line)
        for line in (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
    ]
    selected, sources, hashes, payloads = {}, {}, {}, {}
    for variant in ("sensitivity", "sensitivity_diff", "spot"):
        record, copies = (
            choose_canonical(records, year)
            if variant == "spot"
            else select_canonical(records, variant, year)
        )
        path = (root / record["local_path"]).resolve()
        if not path.is_relative_to(root / "data/raw") or sha256(path) != record["sha256"]:
            raise ValueError("入力raw領域またはSHA不一致")
        for copy in copies:
            if sha256(root / copy["local_path"]) != copy["sha256"]:
                raise ValueError("同SHAコピーのハッシュ不一致")
        selected[variant] = record
        hashes[variant] = record["sha256"]
        payloads[variant] = path.read_bytes()
        sources[variant] = {
            **{
                k: record.get(k)
                for k in (
                    "source_id",
                    "local_path",
                    "original_filename",
                    "source_url",
                    "downloaded_at",
                    "sha256",
                )
            },
            "selection_basis": "manifestの公式年度downloaded版を一意に選択。同SHAコピーは保持",
            "usage": "baseline_QA_only" if variant == "spot" else "processed_input",
            "noncanonical_copies": [
                {"local_path": r["local_path"], "sha256": r["sha256"]} for r in copies
            ],
        }
    table, baseline, report = transform(
        payloads["sensitivity"], payloads["sensitivity_diff"], payloads["spot"], hashes, year
    )
    report.update(
        github_issue=13,
        roadmap_id=7,
        sources=sources,
        output_path=str(output),
        raw_unchanged=all(sha256(root / r["local_path"]) == r["sha256"] for r in selected.values()),
    )
    reports = repo / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / f"price_sensitivity_FY{year}_qa.json"
    if table is not None and report["raw_unchanged"]:
        baseline_path = reports / f"price_sensitivity_FY{year}_baseline_qa.csv"
        with baseline_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(baseline[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(baseline)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            pq.write_table(table, stream, compression="zstd")
        saved = pq.read_table(output)
        if not saved.equals(table, check_metadata=True):
            raise ValueError("Parquet再読込不一致")
        report.update(
            processed_written=True,
            processed_sha256=sha256(output),
            parquet_roundtrip_verified=True,
            baseline_qa_path=str(baseline_path),
            baseline_qa_sha256=sha256(baseline_path),
        )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not report["all_passed"] or not report["raw_unchanged"]:
        raise ValueError(f"入力QA不合格。詳細: {report_path}")
    canonical = repo / f"metadata/jepx_price_sensitivity_FY{year}_canonical.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n")
    return report
