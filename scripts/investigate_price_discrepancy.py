"""JEPX基準価格不一致を原値のまま検査する。正式ETL・補正は行わない。"""

import argparse
import csv
import io
import json
import sys
from collections import Counter
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.jepx_spot import NUMERIC_COLUMNS  # noqa: E402
from src.raw_acquisition import detect_encoding  # noqa: E402
from src.time_normalization import parse_date, sha256, time_key  # noqa: E402


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def quantile(values, q):
    position = Decimal(len(values) - 1) * Decimal(q)
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return str(values[lo] + (values[hi] - values[lo]) * (position - lo))


def load(root, record):
    path = root / record["local_path"]
    if sha256(path) != record["sha256"]:
        raise ValueError("manifest SHA不一致")
    text, encoding = detect_encoding(path.read_bytes())
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = list(reader)
    keys = [
        (r.get("受渡日", r.get("年月日")).replace("/", "-"), int(r["時刻コード"])) for r in rows
    ]
    if len(set(keys)) != len(rows):
        raise ValueError("元キー重複")
    evidence = {
        k: record.get(k)
        for k in (
            "local_path",
            "source_id",
            "variant",
            "status",
            "original_filename",
            "source_url",
            "final_url",
            "http_method",
            "request_parameters",
            "downloaded_at",
            "registered_at",
            "file_size",
            "sha256",
            "period_start",
            "period_end",
            "phase",
        )
    }
    evidence.update(
        encoding_detection=encoding,
        row_count=len(rows),
        first_row=rows[0],
        last_row=rows[-1],
        source_columns=reader.fieldnames,
        http_mime_type=record.get("inspection", {}).get("http_mime_type"),
        original_http_headers=record.get("response_headers"),
        http_note="取得時の完全なHTTP応答ヘッダーは未保存。現在HTTP情報で代用しない",
        raw_sha256_verified=True,
    )
    return dict(zip(keys, rows, strict=True)), evidence


def run(root, repo):
    records = list(
        map(json.loads, (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines())
    )
    selected = {}
    for variant in ("spot", "sensitivity", "sensitivity_diff"):
        candidates = [
            r
            for r in records
            if r.get("variant") == variant
            and r["status"] == "downloaded"
            and r.get("period_start") == "2025-04-01"
            and r.get("period_end") == "2026-03-31"
        ]
        if not candidates or len({r["sha256"] for r in candidates}) != 1:
            raise ValueError(f"{variant}: FY2025公式年度版を一意に選べない")
        selected[variant] = candidates[0]
    series, raw_checks = {}, []
    for variant, record in selected.items():
        series[variant], evidence = load(root, record)
        copies = [
            r
            for r in records
            if r["status"] in ("downloaded", "imported")
            and r.get("sha256") == record["sha256"]
            and r is not record
        ]
        evidence["same_sha_copies"] = []
        for copy in copies:
            _, check = load(root, copy)
            evidence["same_sha_copies"].append(check)
        raw_checks.append(evidence)
    spot, actual, difference = (series[v] for v in ("spot", "sensitivity", "sensitivity_diff"))
    if not set(spot) == set(actual) == set(difference):
        raise ValueError("3系列のキー不一致。黙ってdropしない")
    fy_start = parse_date("2025-04-01")
    expected_keys = {
        (str(fy_start + timedelta(days=day)), period)
        for day in range(365)
        for period in range(1, 49)
    }
    if set(spot) != expected_keys:
        raise ValueError("FY2025の365日×48コマと一致しない")
    keys = sorted(spot)
    areas = {
        n.removeprefix("area_price_"): c
        for n, c in NUMERIC_COLUMNS.items()
        if n.startswith("area_price_")
    }
    a = {k: Decimal(r["システムプライス(円/kWh)"]) for k, r in spot.items()}
    b = {k: Decimal(r["システムプライス"]) for k, r in actual.items()}
    c = {k: Decimal(r["システムプライス"]) for k, r in difference.items()}
    bad = [k for k in keys if a[k] != b[k]]
    details = []
    for key in bad:
        prices = {name: Decimal(spot[key][column]) for name, column in areas.items()}
        delta = a[key] - b[key]
        details.append(
            {
                "delivery_date": key[0],
                "period": key[1],
                "spot_system_price_A": a[key],
                "sensitivity_system_price_B": b[key],
                "difference_file_system_price_C_raw": c[key],
                "A_minus_B": delta,
                "B_minus_C_raw": b[key] - c[key],
                "absolute_difference": abs(delta),
                "relative_absolute_difference_to_A": abs(delta) / abs(a[key]) if a[key] else "",
                **{f"area_price_{name}": v for name, v in prices.items()},
                "areas_matching_B": "|".join(n for n, v in prices.items() if v == b[key]),
                "area_price_dispersion_observed": len(set(prices.values())) > 1,
                "any_area_differs_from_system_A": any(v != a[key] for v in prices.values()),
                "area_price_range": max(prices.values()) - min(prices.values()),
                "C_interpretation": "差分CSVの元値。価格水準の基準価格とみなさない",
            }
        )
    out = repo / "reports"
    write_csv(out / "price_discrepancy_details.csv", details)
    bad_set = set(bad)
    daily_counts = Counter(k[0] for k in bad)
    days = sorted({k[0] for k in keys})
    daily = [
        {
            "delivery_date": day,
            "mismatch_count": daily_counts[day],
            "total_periods": 48,
            "all_48_mismatch": daily_counts[day] == 48,
        }
        for day in days
    ]
    write_csv(out / "price_discrepancy_daily.csv", daily)
    monthly_counts = Counter(k[0][:7] for k in bad)
    monthly_total = Counter(k[0][:7] for k in keys)
    monthly = [
        {
            "month": month,
            "mismatch_count": monthly_counts[month],
            "total_periods": n,
            "mismatch_rate": monthly_counts[month] / n,
        }
        for month, n in sorted(monthly_total.items())
    ]
    write_csv(out / "price_discrepancy_monthly.csv", monthly)
    period_counts = Counter(k[1] for k in bad)
    periods = [
        {
            "period": p,
            "mismatch_count": period_counts[p],
            "total_days": len(days),
            "mismatch_rate": period_counts[p] / len(days),
        }
        for p in range(1, 49)
    ]
    write_csv(out / "price_discrepancy_by_period.csv", periods)
    runs = []
    begin = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or (keys[i] in bad_set) != (keys[begin] in bad_set):
            start, end = keys[begin], keys[i - 1]
            runs.append(
                {
                    "mismatch": start in bad_set,
                    "start_date": start[0],
                    "start_period": start[1],
                    "end_date": end[0],
                    "end_period": end[1],
                    "start_timestamp": time_key(parse_date(start[0]), start[1])[2],
                    "end_timestamp": time_key(parse_date(end[0]), end[1])[3],
                    "consecutive_periods": i - begin,
                }
            )
            begin = i
    write_csv(out / "price_discrepancy_runs.csv", runs)
    boundaries = [
        {
            "previous_date": before,
            "date": after,
            "previous_mismatch_count": daily_counts[before],
            "mismatch_count": daily_counts[after],
        }
        for before, after in zip(days, days[1:])
        if bool(daily_counts[before]) != bool(daily_counts[after])
    ]
    write_csv(out / "price_discrepancy_day_boundaries.csv", boundaries)
    offset_rows = []
    for cohort_name, cohort in (("all", keys), ("original_mismatches", bad)):
        for day_offset in range(-2, 3):
            for period_offset in range(-2, 3):
                tested = matched = 0
                for key in cohort:
                    day = str(parse_date(key[0]) + timedelta(days=day_offset))
                    target = (day, key[1] + period_offset)
                    if target in a:
                        tested += 1
                        matched += b[key] == a[target]
                offset_rows.append(
                    {
                        "cohort": cohort_name,
                        "date_offset": day_offset,
                        "period_offset_within_day": period_offset,
                        "comparison_count": tested,
                        "matched": matched,
                        "unmatched_keys": len(cohort) - tested,
                        "match_rate": matched / tested if tested else None,
                    }
                )
        for shift in (-48, -2, -1, 1, 2, 48):
            tested = matched = 0
            index = {k: i for i, k in enumerate(keys)}
            for key in cohort:
                j = index[key] + shift
                if 0 <= j < len(keys):
                    tested += 1
                    matched += b[key] == a[keys[j]]
            offset_rows.append(
                {
                    "cohort": cohort_name,
                    "date_offset": "",
                    "period_offset_within_day": f"chronological_{shift}",
                    "comparison_count": tested,
                    "matched": matched,
                    "unmatched_keys": len(cohort) - tested,
                    "match_rate": matched / tested if tested else None,
                }
            )
    write_csv(out / "price_discrepancy_offset_tests.csv", offset_rows)
    transforms = []
    for factor in ("1.10", "1.08", "0.5", "2", "0.001", "1000"):
        f = Decimal(factor)
        transforms.append(
            {
                "candidate": f"A*{factor}",
                "exact_matches": sum(a[k] * f == b[k] for k in bad),
                "cent_half_up_matches": sum(
                    (a[k] * f).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) == b[k] for k in bad
                ),
            }
        )
        transforms.append(
            {
                "candidate": f"A/{factor}",
                "exact_matches": sum(a[k] / f == b[k] for k in bad),
                "cent_half_up_matches": sum(
                    (a[k] / f).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) == b[k] for k in bad
                ),
            }
        )
    for digits in (0, 1, 2):
        for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_DOWN):
            q = Decimal(1).scaleb(-digits)
            transforms.append(
                {
                    "candidate": f"round_both_{digits}_{mode}",
                    "exact_matches": sum(
                        a[k].quantize(q, rounding=mode) == b[k].quantize(q, rounding=mode)
                        for k in bad
                    ),
                    "cent_half_up_matches": "",
                }
            )
    write_csv(out / "price_discrepancy_rounding_tax_unit_tests.csv", transforms)
    abs_values = sorted(abs(a[k] - b[k]) for k in bad)
    relative_values = sorted(abs(a[k] - b[k]) / abs(a[k]) for k in bad if a[k])
    counts = Counter(str(a[k] - b[k]) for k in bad)
    summary = {
        "classification": "C_unresolved_pending_official_cause",
        "mismatch_count": len(bad),
        "total_keys": len(keys),
        "key_match_rate": 1.0,
        "unmatched_counts": {v: 0 for v in series},
        "first_mismatch": bad[0],
        "last_mismatch": bad[-1],
        "monthly": monthly,
        "days_with_mismatch": len(daily_counts),
        "all_48_mismatch_days": [d for d in days if daily_counts[d] == 48],
        "longest_mismatch_run": max(
            (r for r in runs if r["mismatch"]), key=lambda r: r["consecutive_periods"]
        ),
        "last_consistent_run": runs[-1],
        "periods": periods,
        "absolute_difference_min": str(abs_values[0]),
        "absolute_difference_max": str(abs_values[-1]),
        "absolute_difference_median": str(abs_values[len(abs_values) // 2]),
        "absolute_difference_mean": str(sum(abs_values) / len(abs_values)),
        "absolute_difference_quantiles": {
            q: quantile(abs_values, q) for q in ("0.5", "0.9", "0.95", "0.99")
        },
        "relative_difference_max": str(relative_values[-1]),
        "relative_difference_median": quantile(relative_values, "0.5"),
        "signed_direction": {
            "A_greater_B": sum(a[k] > b[k] for k in bad),
            "A_less_B": sum(a[k] < b[k] for k in bad),
        },
        "all_areas_equal_A_among_mismatches": sum(
            all(Decimal(spot[k][column]) == a[k] for column in areas.values()) for k in bad
        ),
        "signed_difference_counts": dict(counts.most_common()),
        "within_001_count": sum(v <= Decimal(".01") for v in abs_values),
        "within_010_count": sum(v <= Decimal(".10") for v in abs_values),
        "area_price_matches": {
            name: sum(name in r["areas_matching_B"].split("|") for r in details) for name in areas
        },
        "matches_any_area": sum(bool(r["areas_matching_B"]) for r in details),
        "area_dispersion_count": sum(r["area_price_dispersion_observed"] for r in details),
        "difference_file_C_unique": sorted({str(v) for v in c.values()}),
        "C_caution": "同名の元列は0.00。価格水準の基準列と同一視しない",
        "relative_difference_formula": "abs(A-B)/abs(A)、A=0はnull。補正には使用しない",
        "market_split_caution": "エリア価格差の検査のみ。正式な分断や原因の判定ではない",
        "raw_unchanged": all(
            sha256(root / r["local_path"]) == r["sha256"] for r in selected.values()
        ),
        "processed_written": False,
    }
    (out / "price_discrepancy_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    (out / "price_discrepancy_raw_audit.json").write_text(
        json.dumps(raw_checks, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.data_root, Path(__file__).resolve().parents[1])
    print(
        {
            k: result[k]
            for k in (
                "mismatch_count",
                "first_mismatch",
                "last_mismatch",
                "matches_any_area",
                "absolute_difference_max",
            )
        }
    )
