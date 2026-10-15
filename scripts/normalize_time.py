"""manifestで選んだ原ファイルへ時間列を追加し、interimと軽量QAを出力する。"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.time_normalization import (  # noqa: E402
    CONTRACTS,
    JST,
    TimeAudit,
    fiscal_dates,
    normalize_file,
    select_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--fiscal-year", type=int, default=2025)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_root.resolve()
    records = [
        json.loads(line)
        for line in (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
    ]
    selected = select_records(records, args.fiscal_year)
    stamp = datetime.now(JST).strftime("%Y%m%dT%H%M%S")
    output = args.output or root / "data/interim/time_normalization" / stamp
    if output.exists():
        raise FileExistsError(output)
    audits = {v: TimeAudit(fiscal_dates(args.fiscal_year)) for v in CONTRACTS}
    files = []
    for record in selected:
        files.append(normalize_file(record, root, output, audits[record["variant"]]))
        print(f"{record['variant']}: {record['local_path']}", flush=True)
    results = {variant: audit.result() for variant, audit in audits.items()}
    report = {
        "github_issue": 8,
        "roadmap_issue": 5,
        "fiscal_year": args.fiscal_year,
        "timezone": JST.key,
        "created_at": datetime.now(JST).isoformat(),
        "output_root": str(output.resolve()),
        "file_count": len(files),
        "rows": sum(f["rows"] for f in files),
        "files": files,
        "sources": results,
        "all_passed": all(r["passed"] for r in results.values()),
        "excluded": "サンプル・手動コピー・REI地域別・HJKS。対象行のdrop/数値変換なし",
        "raw_unchanged": all(f["raw_unchanged"] for f in files),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("file_count", "rows", "all_passed", "raw_unchanged")},
            ensure_ascii=False,
        )
    )
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
