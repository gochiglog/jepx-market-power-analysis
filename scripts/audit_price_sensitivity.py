"""原値を変更せず価格感応度の事前QAを実行する。processed出力は行わない。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.price_sensitivity_audit import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.data_root, Path(__file__).resolve().parents[1])
    print({k: result[k] for k in ("status", "spot_price_mismatch_count", "processed_written")})
