"""取得済み原ファイルの翌々日策定版・年間365日・48コマ・キー一意性を監査する。"""

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.acquire_capacity import VARIANT  # noqa: E402
from src.interconnector_capacity import validate_capacity  # noqa: E402
from src.raw_acquisition import (  # noqa: E402
    FY_END,
    FY_START,
    date_range,
    detect_encoding,
    now,
    read_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def audit_capacity(root):
    records = [
        r
        for r in read_manifest(root / "metadata/acquisition_manifest.jsonl")
        if r["status"] == "downloaded" and r["variant"] == VARIANT
    ]
    keys = set()
    dates = Counter()
    lines = defaultdict(set)
    nonnumeric = Counter()
    for r in records:
        path = (root / r["local_path"]).resolve()
        if not path.is_relative_to(root.resolve() / "data/raw"):
            raise ValueError("空容量監査の保存領域外")
        data = path.read_bytes()
        if len(data) != r["file_size"] or hashlib.sha256(data).hexdigest() != r["sha256"]:
            raise ValueError("空容量のサイズ/SHA256不一致")
        result = validate_capacity(data, r["requested_period_start"], r["requested_period_end"])
        nonnumeric.update(result["capacity_audit"]["non_numeric_capacity_value_counts"])
        text, _ = detect_encoding(data)
        for row in csv.DictReader(io.StringIO(text)):
            day = row["年月日"].replace("/", "-")
            key = (
                day,
                row["時刻"],
                row["連系線"],
                row["方向"],
                row["対象断面"],
                row["策定／更新後"],
            )
            if key in keys:
                raise ValueError("年間取得ファイル間で空容量キー重複")
            keys.add(key)
            dates[day] += 1
            lines[day].add(row["連系線"])
    expected = set(date_range(FY_START, FY_END))
    return {
        "audited_at": now(),
        "file_count": len(records),
        "row_count": len(keys),
        "observed_date_count": len(dates),
        "missing_dates": sorted(expected - set(dates)),
        "unexpected_dates": sorted(set(dates) - expected),
        "duplicate_keys": 0,
        "all_groups_48_periods_and_both_directions": bool(records),
        "hash_validation": "all_passed",
        "nonnumeric_capacity_value_counts": dict(nonnumeric),
        "interconnectors_by_date": {d: sorted(lines[d]) for d in sorted(lines)},
        "rows_by_date": dict(sorted(dates.items())),
        "notes": [
            "固定11区間は仮定しない。年度内の連系線構成を日ごとに記録",
            "空容量以外の数値列の欠損と制度上の意味は後続ETLで確認",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_capacity(ROOT)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ["interconnectors_by_date", "rows_by_date"]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
