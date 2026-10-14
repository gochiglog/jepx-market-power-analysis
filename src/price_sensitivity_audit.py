"""価格感応度の停止条件を監査する。processedを書かず原値を保持する。"""

import csv
import io
import json
from pathlib import Path

import pyarrow.parquet as pq

from src.jepx_spot import parse_quantity
from src.raw_acquisition import detect_encoding
from src.time_normalization import TimeAudit, fiscal_dates, parse_date, sha256, time_key

SCENARIOS = ("売500MW", "買500MW", "売1000MW", "買1000MW", "売5000MW", "買5000MW")


def select_canonical(records: list[dict], variant: str, year=2025):
    start, end = fiscal_dates(year)[0], fiscal_dates(year)[-1]
    candidates = [
        r
        for r in records
        if r["source_id"] == "jepx_price_sensitivity"
        and r["status"] in ("downloaded", "imported")
        and r.get("period_start") == str(start)
        and r.get("period_end") == str(end)
    ]
    canonical = [
        r for r in candidates if r.get("variant") == variant and r["status"] == "downloaded"
    ]
    if len(canonical) != 1:
        raise ValueError("canonical候補が一意でない。ユーザー判断が必要")
    # 実数/差分を区別し、同じvariantの手動候補だけを照合する。
    suffix = "virtualprice_diff" if variant == "sensitivity_diff" else "virtualprice"
    relevant = [
        r
        for r in candidates
        if r.get("variant") == variant
        or (
            suffix in (r.get("original_filename") or "")
            and ("diff" in (r.get("original_filename") or "")) == (variant == "sensitivity_diff")
        )
    ]
    if len({r["sha256"] for r in relevant}) != 1:
        raise ValueError("異なるSHAの年度候補。自動採用せずユーザー確認")
    return canonical[0], [r for r in relevant if r is not canonical[0]]


def inspect(data: bytes, year=2025):
    text, encoding = detect_encoding(data)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    expected = ["年月日", "時刻コード", "システムプライス", *SCENARIOS]
    names = reader.fieldnames or []
    if set(names) != set(expected) or len(names) != len(expected):
        raise ValueError("未対応のraw schema")
    audit = TimeAudit(fiscal_dates(year))
    rows, failures, nulls = {}, [], []
    for number, row in enumerate(reader, 2):
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"raw列数不一致: CSV論理行{number}")
        day, period = parse_date(row["年月日"]), int(row["時刻コード"])
        time_key(day, period)
        key = (str(day), period)
        if key in rows:
            raise ValueError("raw重複キー。行を削除せず停止")
        audit.observe(day, (), (period,), "30min")
        values = {}
        for column in ["システムプライス", *SCENARIOS]:
            try:
                values[column] = parse_quantity(row[column])
                if values[column] is None:
                    nulls.append({"date": str(day), "period": period, "column": column})
            except ValueError:
                failures.append(
                    {"date": str(day), "period": period, "column": column, "value": row[column]}
                )
        rows[key] = values
    return rows, {
        "time_qa": audit.result(),
        "encoding": encoding,
        "numeric_parse_failures": failures,
        "nulls": nulls,
        "missing_rates": {
            c: sum(x["column"] == c for x in nulls) / len(rows)
            for c in ["システムプライス", *SCENARIOS]
        },
    }


def compare(actual: dict, difference: dict, spot: dict):
    sets = [set(actual), set(difference), set(spot)]
    common = sets[0] & sets[1] & sets[2]
    matches = {
        "virtual_minus_sensitivity_base": 0,
        "sensitivity_base_minus_virtual": 0,
        "absolute_difference": 0,
    }
    mismatch = []
    formula_mismatch = []
    for key in sorted(common):
        base = actual[key]["システムプライス"]
        if base != spot[key]:
            mismatch.append(
                {
                    "delivery_date": key[0],
                    "period": key[1],
                    "spot_price": str(spot[key]),
                    "sensitivity_price": str(base),
                    "difference_sensitivity_minus_spot": str(base - spot[key]),
                }
            )
        for column in SCENARIOS:
            virtual, published = actual[key][column], difference[key][column]
            matches["virtual_minus_sensitivity_base"] += virtual - base == published
            matches["sensitivity_base_minus_virtual"] += base - virtual == published
            matches["absolute_difference"] += abs(virtual - base) == published
            if virtual - base != published:
                formula_mismatch.append({"date": key[0], "period": key[1], "column": column})
    return {
        "key_match": {
            "actual_rows": len(actual),
            "difference_rows": len(difference),
            "spot_rows": len(spot),
            "matched": len(common),
            "actual_unmatched": len(sets[0] - common),
            "difference_unmatched": len(sets[1] - common),
            "spot_unmatched": len(sets[2] - common),
            "actual_match_rate": len(common) / len(actual),
        },
        "candidate_formula_match_counts": matches,
        "scenario_comparisons": len(common) * 6,
        "candidate_formula_mismatches": formula_mismatch,
        "spot_price_match_count": len(common) - len(mismatch),
        "spot_price_match_rate": (len(common) - len(mismatch)) / len(common),
        "spot_price_mismatch_count": len(mismatch),
        "spot_price_mismatches": mismatch,
        "formula_officially_verified": False,
        "status": "stopped_pending_research_decision",
        "processed_written": False,
    }


def run(root: Path, repo: Path, year=2025):
    records = list(
        map(json.loads, (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines())
    )
    data, sources, checks = {}, {}, {}
    for variant in ("sensitivity", "sensitivity_diff"):
        record, copies = select_canonical(records, variant, year)
        path = root / record["local_path"]
        if sha256(path) != record["sha256"]:
            raise ValueError("raw SHA不一致")
        data[variant], checks[variant] = inspect(path.read_bytes(), year)
        if checks[variant]["numeric_parse_failures"] or checks[variant]["nulls"]:
            raise ValueError("数値parse/null異常。公開値を確認するまで比較停止")
        sources[variant] = {
            "local_path": record["local_path"],
            "sha256": record["sha256"],
            "original_filename": record["original_filename"],
            "source_url": record["source_url"],
            "noncanonical_copies": [r["local_path"] for r in copies],
        }
    spot_path = root / f"data/processed/jepx/spot/jepx_spot_FY{year}.parquet"
    table = pq.read_table(spot_path)
    spot = {
        (str(d), p): price
        for d, p, price in zip(
            table["delivery_date"].to_pylist(),
            table["period"].to_pylist(),
            table["system_price"].to_pylist(),
            strict=True,
        )
    }
    if len(spot) != table.num_rows:
        raise ValueError("spotの重複キー")
    result = compare(data["sensitivity"], data["sensitivity_diff"], spot)
    result.update(
        {
            "github_issue": 13,
            "roadmap_id": 7,
            "checks": checks,
            "sources": sources,
            "spot_processed_sha256": sha256(spot_path),
            "raw_unchanged": all(
                sha256(root / r["local_path"]) == r["sha256"] for r in sources.values()
            ),
            "difference_system_price_unique": sorted(
                {str(r["システムプライス"]) for r in data["sensitivity_diff"].values()}
            ),
        }
    )
    report = repo / f"reports/price_sensitivity_FY{year}_preflight_qa.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    csv_path = repo / f"reports/price_sensitivity_FY{year}_spot_mismatches.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "delivery_date",
                "period",
                "spot_price",
                "sensitivity_price",
                "difference_sensitivity_minus_spot",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(result["spot_price_mismatches"])
    return result
