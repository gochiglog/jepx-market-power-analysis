"""保存済み比較表を原表と独立に全件照合する。出力データは変更しない。"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.comparison_hourly import HOUR_KEY, PRICES  # noqa: E402
from src.comparison_interconnector import annotate_comparison  # noqa: E402
from src.decision_preparation import NATIVE, NETWORK_FILES, frame_table  # noqa: E402
from src.generation_hjks import read_raw  # noqa: E402
from src.time_normalization import sha256  # noqa: E402


def validate(root, repo):
    manifest = json.loads(
        (repo / "metadata/decision_preparation_manifest_20261016.json").read_text()
    )
    output = root / Path(manifest["outputs"][0]["path"]).parent
    for entry in manifest["inputs"]:
        base = root if entry["location"] == "data_root" else repo
        if sha256(base / entry["path"]) != entry["sha256"]:
            raise ValueError("入力manifest SHA不一致")
    for entry in manifest["outputs"]:
        if sha256(root / entry["path"]) != entry["sha256"]:
            raise ValueError("出力manifest SHA不一致")
    original = pq.read_table(root / NATIVE / "market_base_FY2025_with_reserve.parquet")
    market = original.to_pandas()
    hour_table = pq.read_table(output / "market_hourly_price_demand_FY2025.parquet")
    hourly = hour_table.to_pandas()
    if len(hourly) != 78840 or hourly.duplicated(HOUR_KEY).any():
        raise ValueError("1時間キー不正")
    for half in (1, 2):
        wanted = market[market.period.mod(2).eq(half % 2)].copy()
        wanted["hour_of_delivery"] = (wanted.period - 1) // 2
        wanted = wanted.set_index(HOUR_KEY).loc[pd.MultiIndex.from_frame(hourly[HOUR_KEY])]
        for field in original.schema:
            name = field.name if field.name in HOUR_KEY else f"halfhour_{half}__{field.name}"
            source_values = (
                hourly[field.name].tolist()
                if field.name in HOUR_KEY
                else wanted[field.name].tolist()
            )
            expected = pa.array(source_values, type=field.type, from_pandas=True)
            actual = hour_table[name].combine_chunks()
            if not actual.equals(expected):
                raise ValueError(f"元30分全セル・型の不一致: {name}")
    for price in PRICES:
        first, second = (hourly[f"halfhour_{h}__{price}"] for h in (1, 2))
        expected = (first + second) / Decimal(2)
        if not expected.equals(hourly[f"{price}__time_mean"]):
            raise ValueError("等時間平均不一致")
        if not (second - first).equals(hourly[f"{price}__second_minus_first"]):
            raise ValueError("価格差不一致")
    for i, name in enumerate(NETWORK_FILES):
        original = pq.read_table(root / NATIVE / name)
        added, _ = annotate_comparison(original.to_pandas(), market, system=i == 0)
        normalized = frame_table(
            added, {c: pa.decimal128(38, 6) for c in added if c.endswith(("_price", "_mw"))}
        )
        target = pq.read_table(output / name.replace("_with_directions_", "_direction_comparison_"))
        if not target.select(original.column_names).equals(original, check_metadata=True):
            raise ValueError("連系線の元セル・型が不一致")
        if not target.select(normalized.column_names).equals(normalized):
            raise ValueError("連系線の表示値・価格参照が不一致")
    prior = json.loads((repo / "reports/generation_hjks_FY2025_qa.json").read_text())
    snapshot = prior["snapshots"]["hjks_unit_677"]["source"]
    _, raw, _ = read_raw(root, snapshot)
    lookup = {f"{snapshot['sha256']}:{n}": r for n, r, _ in raw}
    edges = pq.read_table(output / "hjks_business_candidate_rows_FY2025.parquet").to_pandas()
    for entry in edges.to_dict("records"):
        for key, value in lookup[entry["hjks_snapshot_row_id"]].items():
            if entry["hjks_raw__" + key] != value:
                raise ValueError("HJKS候補元セル不一致")
    return dict(
        hourly_rows=len(hourly),
        halfhour_source_columns_each=len(market.columns),
        network_original_and_added_rows=807888,
        hjks_candidate_rows=len(edges),
        inputs_sha_verified=len(manifest["inputs"]),
        outputs_sha_verified=len(manifest["outputs"]),
        all_passed=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--qa-output", type=Path)
    args = parser.parse_args()
    result = validate(args.data_root.resolve(), Path(__file__).resolve().parents[1])
    if args.qa_output:
        args.qa_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
