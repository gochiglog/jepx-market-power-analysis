"""manifest・SHA256・対象日の収録状況を監査する。データの整形・結合はしない。"""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.raw_acquisition import FY_END, FY_START, date_range, now, read_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def audit(root):
    records = read_manifest(root / "metadata/acquisition_manifest.jsonl")
    schema = json.loads((root / "metadata/acquisition_manifest.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    catalog = yaml.safe_load((root / "metadata/data_sources.yml").read_text())
    ids = {s["source_id"] for s in catalog["sources"]}
    files = set()
    groups = defaultdict(list)
    failures = []
    warnings = []
    for record in records:
        validator.validate(record)
        if record["source_id"] not in ids:
            raise ValueError(f"manifestの不明なソース: {record['source_id']}")
        if record["status"] == "failed":
            failures.append({"variant": record["variant"], "notes": record["notes"]})
            continue
        path = (root / record["local_path"]).resolve()
        if not path.is_relative_to(root.resolve() / "data/raw"):
            raise ValueError("manifestパスがraw外へ解決される")
        if record["local_path"] in files:
            raise ValueError(f"manifestファイルキー重複: {record['local_path']}")
        files.add(record["local_path"])
        data = path.read_bytes()
        if len(data) != record["file_size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"rawサイズ/SHA256不一致: {record['local_path']}")
        inspected = record["inspection"]
        if inspected.get("malformed_rows") or inspected.get("unparsed_date_rows"):
            warnings.append(f"{record['local_path']}: 不正列数または対象日未解釈行あり")
        periods = inspected.get("period_audit", {})
        if any(
            periods.get(k)
            for k in ["duplicate_date_period_keys", "invalid_period_rows", "incomplete_dates"]
        ):
            warnings.append(f"{record['local_path']}: コマキーに不整合あり")
        groups[record["variant"]].append(record)
    physical = {
        str(path.relative_to(root)) for path in (root / "data/raw").rglob("*") if path.is_file()
    }
    # 既存rawにmanifestが無くても変更しない。未登録として報告する。
    unregistered = sorted(physical - files)
    expected = set(date_range(FY_START, FY_END))
    variants = {}
    for variant, items in sorted(groups.items()):
        dates = {day for item in items for day in item["inspection"].get("observed_dates", [])}
        formats = [
            {
                key: item["inspection"].get(key)
                for key in [
                    "extension",
                    "http_mime_type",
                    "encoding",
                    "bom",
                    "encoding_candidates",
                    "delimiter",
                    "header_row",
                    "observed_time_labels",
                ]
            }
            for item in items
        ]
        unique_formats = list(
            {json.dumps(f, ensure_ascii=False, sort_keys=True): f for f in formats}.values()
        )
        variants[variant] = {
            "source_id": items[0]["source_id"],
            "file_count": len(items),
            "file_size": sum(item["file_size"] for item in items),
            "observed_date_count": len(dates & expected),
            "period_start": min(dates) if dates else None,
            "period_end": max(dates) if dates else None,
            "missing_dates": sorted(expected - dates) if dates else None,
            "coverage_status": ("all_dates_observed" if expected <= dates else "partial")
            if dates
            else "unverified_snapshot",
            "formats": unique_formats,
        }
    unresolved = [f for f in failures if f["variant"] not in groups]
    sources = {}
    for source in catalog["sources"]:
        sid = source["source_id"]
        matched = [v for v, info in variants.items() if info["source_id"] == sid]
        sources[sid] = {
            "variants": matched,
            "file_count": sum(variants[v]["file_count"] for v in matched),
            "notes": source.get("acquisition", {}).get("known_limitations", []),
        }
    return {
        "audited_at": now(),
        "baseline_start": FY_START.isoformat(),
        "baseline_end": FY_END.isoformat(),
        "downloaded_file_count": len(files),
        "downloaded_bytes": sum(r["file_size"] for r in records if r["status"] == "downloaded"),
        "hash_validation": "all_passed",
        "unregistered_raw_paths": unregistered,
        "warnings": warnings,
        "variants": variants,
        "sources": sources,
        "unresolved_failures": unresolved,
        "failed_attempt_count": len(failures),
        "value_completeness_validation": (
            "未実施。対象日収録はエリア・ユニット・値の完全性を保証しない"
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(ROOT)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"variants", "sources"}},
            ensure_ascii=False,
            indent=2,
        )
    )
