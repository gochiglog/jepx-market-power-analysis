"""RD-008に基づきJEPX価格感応度を原系列のままParquetへ保存する。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.jepx_price_sensitivity import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--fiscal-year", type=int, default=2025)
    args = parser.parse_args()
    result = run(args.data_root, Path(__file__).resolve().parents[1], args.fiscal_year)
    print(
        {k: result[k] for k in ("status", "processed_rows", "processed_written", "raw_unchanged")}
    )
