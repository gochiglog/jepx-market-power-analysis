"""HJKS台帳の元版・元行を保持して、未確定の資産帰属候補を出力する。"""

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.raw_acquisition import detect_encoding  # noqa: E402


def build(data_root: Path, research: Path):
    operators = {
        (r["operator_raw"], r["area_raw"]): r
        for r in csv.DictReader(
            (research / "operator_mapping_candidates.csv").open(encoding="utf-8")
        )
    }
    records = list(
        map(
            json.loads, (data_root / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
        )
    )
    assets = []
    for record in records:
        if record["source_id"] != "hjks_unit" or record["status"] not in ("downloaded", "imported"):
            continue
        data = (data_root / record["local_path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("HJKS台帳SHA不一致")
        text, _ = detect_encoding(data)
        reader = csv.reader(io.StringIO(text, newline=""))
        headers = next(reader)
        for number, cells in enumerate(reader, 2):
            if len(cells) != len(headers) and not (
                len(cells) == len(headers) + 1 and cells[-1] == ""
            ):
                raise ValueError("既知の末尾空セル以外の台帳列数異常")
            row = dict(zip(headers, cells))
            op = operators[(row["発電事業者"], row["エリア"])]
            candidate = op["regulatory_entity"]
            review = candidate or op["mapping_confidence"] == "manual_review"
            assets.append(
                {
                    "plant_code": row["発電所コード"],
                    "plant_name": row["発電所名"],
                    "unit_name": row["ユニット名"],
                    "area": row["エリア"],
                    "operator_raw": row["発電事業者"],
                    "generation_company": op["generation_company"],
                    "corporate_group": op["corporate_group"],
                    "regulatory_entity_candidate": candidate,
                    "mapping_status": "manual_review" if review else "unresolved",
                    "mapping_basis": (
                        "operator名×エリアの候補。資産・供給契約・時点は未確認。"
                        "グループ帰属で規制対応を確定しない"
                    ),
                    "valid_from": "",
                    "valid_to": "",
                    "notes": (
                        "別法人比較先："
                        + op["regulatory_entity_comparison_candidate"]
                        + "。稼働終了ユニットも保持。出力変更の適用開始日をmapping期間へ流用しない"
                    ),
                    "operator_name_confidence": op["mapping_confidence"],
                    "source_path": record["local_path"],
                    "source_sha256": record["sha256"],
                    "source_row_number": number,
                    "operation_start_raw": row["稼働開始日"],
                    "operation_end_raw": row["稼働終了日"],
                    "capacity_effective_date_raw": row["適用開始日"],
                }
            )
    if not assets:
        raise ValueError("HJKS台帳がない")
    with (research / "asset_regulatory_mapping_candidates.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(assets[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(assets)
    summary = {
        "phase_a": "complete_user_approved",
        "regulatory_entities": 10,
        "regulatory_entity_area_pairs": 11,
        "phase_b": "pending_generation_hjks_etl",
        "asset_snapshot_rows": len(assets),
        "mapping_status_counts": dict(Counter(r["mapping_status"] for r in assets)),
        "unresolved_asset_mapping_rows": len(assets),
        "unique_observed_asset_keys": len(
            {
                (r["plant_code"], r["plant_name"], r["unit_name"], r["area"], r["operator_raw"])
                for r in assets
            }
        ),
        "snapshot_copies_merged": False,
        "dominant_asset_flag_assigned": False,
        "fringe_flag_assigned": False,
    }
    (research / "issue_3_phase_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.data_root, Path(__file__).resolve().parents[1] / "metadata/research"))
