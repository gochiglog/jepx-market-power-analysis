"""確認済みサンプルを前提に、公式元ファイルを取得する。"""

import argparse
import hashlib
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.raw_acquisition import (  # noqa: E402
    FY_END,
    FY_START,
    append_manifest,
    now,
    read_manifest,
    record_download,
)
from src.source_downloads import RECIPES, download_recipe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def manifest_records(root):
    path = root / "metadata" / "acquisition_manifest.jsonl"
    return read_manifest(path)


def sample_verified(records, variant):
    return any(
        r.get("variant") == variant
        and r.get("phase") == "sample"
        and r["status"] == "downloaded"
        and r["inspection"].get("row_count", 0) > 0
        and r["inspection"].get("observed_dates")
        and not r["inspection"].get("malformed_rows")
        and not r["inspection"].get("unparsed_date_rows")
        and not r.get("missing_dates")
        and not any(
            r["inspection"].get("period_audit", {}).get(k)
            for k in ["duplicate_date_period_keys", "invalid_period_rows", "incomplete_dates"]
        )
        for r in records
    )


def verify_existing(root, record):
    path = (root / record["local_path"]).resolve()
    if not path.is_relative_to(root.resolve() / "data" / "raw"):
        raise ValueError("manifestのrawパスが保存領域外")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError(f"既存rawのSHA256不一致: {record['local_path']}")


def month_ranges():
    start = FY_START
    while start <= FY_END:
        next_month = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        end = min(next_month - timedelta(days=1), FY_END)
        yield start.isoformat(), end.isoformat()
        start = next_month


def acquire(root, variant, phase):
    recipe = RECIPES[variant]
    sid = recipe["source_id"]
    catalog = yaml.safe_load((root / "metadata" / "data_sources.yml").read_text())
    name = next(s["name"] for s in catalog["sources"] if s["source_id"] == sid)
    records = manifest_records(root)
    for record in records:
        if record.get("variant") == variant and record["status"] == "downloaded":
            verify_existing(root, record)
    if phase == "full" and recipe["adapter"] not in {"jepx", "reserve", "generation", "system"}:
        raise ValueError("全期間取得が確認されていないソース。サンプル/手順だけを記録する")
    if phase == "full" and not sample_verified(records, variant):
        raise ValueError("同じソース・種類のサンプルを先に取得・検査する必要がある")
    if recipe["adapter"] == "jepx":
        ranges = [(FY_START.isoformat(), FY_END.isoformat())]
    elif phase == "full":
        ranges = list(month_ranges())
    else:
        ranges = [(FY_START.isoformat(), FY_START.isoformat())]
    failures = 0
    for start, end in ranges:
        if any(
            r.get("variant") == variant
            and r["status"] == "downloaded"
            and (
                (r.get("requested_period_start") == start and r.get("requested_period_end") == end)
                or recipe["adapter"] in {"hjks", "file"}
            )
            for r in records
        ):
            print(f"{variant} {start}〜{end}: 既存取得版を保持（再取得・上書きなし）", flush=True)
            continue
        try:
            data, headers, final, url, fields, filename = download_recipe(recipe, start, end)
            record = record_download(
                root,
                sid,
                name,
                url,
                data,
                headers,
                final,
                filename,
                start if recipe["adapter"] not in {"hjks", "file"} else None,
                end if recipe["adapter"] not in {"hjks", "file"} else None,
                recipe.get("date_column"),
                recipe.get("period_column"),
                fields,
                notes=(
                    "現在取得版。FY2025時点の情報履歴を保証しない"
                    if recipe["adapter"] == "hjks"
                    else "long化・結合・単位変換なし"
                ),
                phase=phase,
                variant=variant,
            )
            records.append(record)
            missing_count = len(record["missing_dates"]) if "missing_dates" in record else "未評価"
            print(
                f"{variant} {start}〜{end}: {record['file_size']} bytes, "
                f"{record['inspection']['encoding']}, "
                f"未収録日={missing_count}",
                flush=True,
            )
        except Exception as error:
            failures += 1
            record = {
                "source_id": sid,
                "source_name": name,
                "source_url": recipe.get("url")
                or {
                    "jepx": "https://www.jepx.jp/_download.php",
                    "reserve": "https://web-kohyo.occto.or.jp/kks-web-public/download",
                    "generation": "https://hatsuden-kokai.occto.or.jp/hks-web-public/info/hks",
                    "system": "https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/CF01S010C",
                    "hjks": f"https://hjks.jepx.or.jp/hjks/{recipe.get('page', '')}",
                }.get(recipe["adapter"]),
                "variant": variant,
                "phase": phase,
                "status": "failed",
                "attempted_at": now(),
                "downloaded_at": None,
                "local_path": None,
                "requested_period_start": start,
                "requested_period_end": end,
                "period_start": None,
                "period_end": None,
                "file_size": None,
                "sha256": None,
                "notes": f"{type(error).__name__}: {error}",
            }
            append_manifest(root / "metadata" / "acquisition_manifest.jsonl", record)
            print(f"{variant}: 取得失敗 {record['notes']}", flush=True)
            # 成功前提が崩れた種類では大量の再試行を行わない。
            break
        time.sleep(0.5)
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", required=True, choices=RECIPES)
    parser.add_argument("--phase", choices=["sample", "full"], default="sample")
    args = parser.parse_args()
    sys.exit(bool(acquire(ROOT, args.variant, args.phase)))
