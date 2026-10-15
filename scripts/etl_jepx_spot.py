"""JEPX spot FY2025のParquet ETLを実行する。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.jepx_spot import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--fiscal-year", type=int, default=2025)
    args = parser.parse_args()
    year = args.fiscal_year
    output = args.data_root / f"data/processed/jepx/spot/jepx_spot_FY{year}.parquet"
    result = run(
        args.data_root,
        output,
        Path(f"reports/jepx_spot_FY{year}_qa.json"),
        Path(f"metadata/jepx_spot_FY{year}_canonical.json"),
        year,
    )
    print(
        {
            "rows": result["processed_rows"],
            "all_passed": result["all_passed"],
            "output": str(output),
        }
    )
