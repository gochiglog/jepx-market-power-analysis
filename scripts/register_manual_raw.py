"""整理済み手動ファイルをmanifestへ登録する。URL・取得日時の不明値は補完しない。"""

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.raw_acquisition import (  # noqa: E402
    append_manifest,
    detect_encoding,
    inspect_bytes,
    now,
    read_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_IDS = {
    "jepx_sensitivity": "jepx_price_sensitivity",
    "jepx_sensitivity_diff": "jepx_price_sensitivity",
    "occto_system_demand": "occto_demand",
    "occto_reserve_block": "occto_reserve",
    "occto_reserve_interconnector": "occto_interconnector",
    "rei_validation": "rei_power_data",
    "egc_regulatory_candidate": "regulatory_entities",
}


def register(root, inventory):
    manifest = root / "metadata/acquisition_manifest.jsonl"
    known = {r["local_path"] for r in read_manifest(manifest) if r.get("local_path")}
    count = 0
    for item in inventory["files"]:
        name = item.get("recommended_path")
        if not name or name in known:
            continue
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve() / "data/raw"):
            raise ValueError("手動登録の保存領域外")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"手動登録前SHA256不一致: {name}")
        sid = SOURCE_IDS.get(item["source_estimate"], item["source_estimate"])
        if sid == "rei_power_data":
            text, e = detect_encoding(data)
            rows = csv.reader(io.StringIO(text))
            headers = [next(rows), next(rows)]
            days = sorted({r[0].replace("/", "-") for r in rows if r})
            inspection = {
                **e,
                "extension": ".csv",
                "http_mime_type": None,
                "format": "csv",
                "bom": e["bom"],
                "delimiter": ",",
                "sheet_names": [],
                "columns": headers[1],
                "header_rows": headers,
                "header_row": 2,
                "row_count": item["row_count"],
                "observed_dates": days,
                "period_start": min(days),
                "period_end": max(days),
                "malformed_rows": 0,
            }
        else:
            inspection = inspect_bytes(
                data,
                path.name,
                None,
                item.get("date_column"),
                "時刻コード" if "jepx" in sid else None,
            )
        if sid in {"hjks_unit", "hjks_outage", "regulatory_entities"}:
            # 時点・適用範囲の確認は別途必要。名前からFYを設定しない。
            inspection["period_start"] = inspection["period_end"] = None
        append_manifest(
            manifest,
            {
                "source_id": sid,
                "source_name": item["source_estimate"],
                "source_url": None,
                "local_path": name,
                "original_local_path": item["current_path"],
                "original_filename": item["original_filename"],
                "http_method": "FILE",
                "period_start": inspection.get("period_start"),
                "period_end": inspection.get("period_end"),
                "requested_period_start": None,
                "requested_period_end": None,
                "downloaded_at": None,
                "registered_at": now(),
                "file_size": len(data),
                "sha256": item["sha256"],
                "status": "imported",
                "phase": "sample",
                "variant": "manual_" + item["source_estimate"] + "_" + path.parent.name,
                "notes": (
                    "ユーザー手動収集。URL・取得日時・公式ダウンロード時の元名は未確認。"
                    "元名は手元名。内容変換なし"
                ),
                "inspection": inspection,
            },
        )
        count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()
    print("手動登録", register(ROOT, json.loads(args.inventory.read_text())), "件")
