"""手動一致サンプルを前提に、連系線空容量のFY2025を7日ずつ原形式で取得する。"""

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.acquire_raw import sample_verified, verify_existing  # noqa: E402
from src.interconnector_capacity import (  # noqa: E402
    DIRECTORY,
    SOURCE_ID,
    URL,
    download_capacity,
    validate_capacity,
    week_ranges,
)
from src.raw_acquisition import append_manifest, now, read_manifest, record_download  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VARIANT = "system_capacity_dayahead2_planned"


def acquire(root, phase):
    manifest = root / "metadata/acquisition_manifest.jsonl"
    records = read_manifest(manifest)
    previous = [r for r in records if r.get("variant") == VARIANT and r["status"] == "downloaded"]
    for r in previous:
        verify_existing(root, r)
    if phase == "full" and not (
        sample_verified(records, VARIANT)
        and any(r.get("manual_comparison", {}).get("byte_identical") for r in previous)
    ):
        raise ValueError("手動CSVとの完全一致サンプルが必要")
    ranges = list(week_ranges()) if phase == "full" else [next(week_ranges())]
    for start, end in ranges:
        if any(
            r["requested_period_start"] == start and r["requested_period_end"] == end
            for r in previous
        ):
            print(f"{start}〜{end}: SHA確認済みの既存取得版を保持", flush=True)
            continue
        try:
            data, headers, final, fields = download_capacity(start, end)
            inspection = validate_capacity(data, start, end)
            filename = (
                "occto_interconnector_capacity_dayahead2_planned_"
                + start.replace("-", "")
                + "_"
                + end.replace("-", "")
                + ".csv"
            )
            manual = root / "data/raw" / DIRECTORY / filename
            comparison = None
            if manual.exists():
                comparison = {
                    "manual_path": str(manual.relative_to(root)),
                    "manual_sha256": hashlib.sha256(manual.read_bytes()).hexdigest(),
                    "byte_identical": manual.read_bytes() == data,
                }
                if not comparison["byte_identical"]:
                    raise ValueError("同期間の手動CSVと元バイト列が一致しない。年間取得を停止")
            if phase == "sample" and not comparison:
                raise ValueError("比較用の手動サンプルが存在しない")
            # 手動版を保持して新規自動取得版を別名で保存する。
            record = record_download(
                root,
                SOURCE_ID,
                "OCCTO系統情報サービス連系線空容量",
                URL,
                data,
                headers,
                final,
                "auto_" + filename,
                start,
                end,
                "年月日",
                fields=fields,
                phase=phase,
                variant=VARIANT,
                save_directory=DIRECTORY,
                inspection_extra=inspection,
                manual_comparison=comparison,
                notes="翌々日・策定・全連系線・両方向。元形式。変換/結合なし",
            )
            previous.append(record)
            print(
                f"{start}〜{end}: {record['file_size']} bytes, "
                f"{inspection['capacity_audit']['row_count']}行, 48コマ/両方向検証済み",
                flush=True,
            )
        except Exception as error:
            append_manifest(
                manifest,
                {
                    "source_id": SOURCE_ID,
                    "source_name": "OCCTO系統情報サービス連系線空容量",
                    "source_url": URL,
                    "variant": VARIANT,
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
                },
            )
            print(f"{start}〜{end}: 取得停止 {error}", flush=True)
            return 1
        time.sleep(1)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["sample", "full"], default="sample")
    args = parser.parse_args()
    sys.exit(acquire(ROOT, args.phase))
