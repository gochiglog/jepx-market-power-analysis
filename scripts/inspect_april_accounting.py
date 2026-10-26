"""公表CSV内の会計を照合。純蓄電出力を総充電へ推定分解しない。"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.actual_net_demand import clock_keys, numeric_raw  # noqa: E402


def run(root, folder):
    target = folder / "primary_csv_accounting_checks.parquet"
    if target.exists():
        raise ValueError("既存会計出力を上書きしない")
    data = pq.read_table(folder / "B30_primary_input_ledger.parquet").to_pandas()
    old = root / "data/interim/occto/actual_net_demand/20261026_preparation_v7"
    result, inputs = [], []
    for area, label in [
        ("北海道", "hokkaido"),
        ("東北", "tohoku"),
        ("東京", "tokyo_actual"),
        ("北陸", "hokuriku"),
        ("関西", "kansai"),
        ("中国", "chugoku"),
        ("四国", "shikoku"),
        ("九州", "kyushu"),
        ("中部", "chubu"),
    ]:
        path = (
            folder / "chubu_april_primary_all_original_columns.parquet"
            if area == "中部"
            else old / f"{label}_202504_all_original_rows.parquet"
        )
        original = pq.read_table(path).to_pandas()
        columns = [
            c
            for c in original.columns
            if c not in {"DATE", "TIME", "エリア需要", "合計"} and "制御量" not in c
        ]
        if area == "中部":
            # 新規表には正規化列もあるので、原CSV20列だけを用いる。
            columns = original.columns[3:19].tolist()
            columns = [c for c in columns if "制御量" not in c]
        padding = original["DATE"].eq("") & original["TIME"].eq("")
        original = original.loc[~padding].reset_index(drop=True)
        keys = clock_keys(original.DATE, original.TIME, role="end" if area == "九州" else "start")
        for i, row in original.iterrows():
            terms = [numeric_raw(row[c]) for c in columns]
            # 九州CSVは連系線が送電正。他8地域は受電正（数値整合/公式説明水準を別表へ）。
            if area == "九州":
                terms[columns.index("連系線")] *= -1
            total = None if any(v is None for v in terms) else sum(terms)
            d = numeric_raw(row["エリア需要"])
            pump, battery = numeric_raw(row["揚水"]), numeric_raw(row["蓄電池"])
            storage = None if pump is None or battery is None else pump + battery
            other = None if total is None or storage is None else total - storage
            result.append(
                {
                    "area": area,
                    "delivery_date": keys.delivery_date.iloc[i],
                    "period": int(keys.period.iloc[i]),
                    "source_file": str(path.relative_to(root)),
                    "source_row": i + 3,
                    "original_component_columns_json": json.dumps(columns, ensure_ascii=False),
                    "unknown_component_count": sum(v is None for v in terms),
                    "component_sum_mw": total,
                    "demand_mw": d,
                    "component_sum_minus_demand_mw": None if total is None else total - d,
                    "storage_net_mw": storage,
                    "nonstorage_plus_import_mw": other,
                    "demand_minus_storage_net_mw": None if storage is None else d - storage,
                    "gross_charging_observed": False,
                }
            )
        inputs.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    frame = pd.DataFrame(result)
    if len(frame) != 12960 or frame.duplicated(["area", "delivery_date", "period"]).any():
        raise ValueError("会計キー不一致")
    check = frame.merge(
        data[["area", "delivery_date", "period", "demand_actual_sample_mw"]],
        on=["area", "delivery_date", "period"],
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    assert check._merge.eq("both").all() and check.demand_mw.eq(check.demand_actual_sample_mw).all()
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, target, compression="zstd")
    assert pq.read_table(target).equals(table)
    qa = root / "reports/20_april_all_supplier_balance/qa"
    frame.groupby("area").agg(
        rows=("period", "size"),
        complete_component_rows=("component_sum_mw", "count"),
        max_absolute_component_residual_mw=(
            "component_sum_minus_demand_mw",
            lambda x: x.dropna().abs().max(),
        ),
        unknown_storage_rows=("storage_net_mw", lambda x: int(x.isna().sum())),
    ).reset_index().to_csv(qa / "primary_accounting_summary.csv", index=False)
    (qa / "accounting_audit.json").write_text(
        json.dumps(
            {
                "inputs": inputs,
                "outputs": [
                    {
                        "path": str(target.relative_to(root)),
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "rows": len(frame),
                    }
                ],
                "join_matches": 12960,
                "unmatched": 0,
                "gross_charging_reconstructed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=root / ("data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1"),
    )
    args = parser.parse_args()
    run(root, args.output.resolve())
