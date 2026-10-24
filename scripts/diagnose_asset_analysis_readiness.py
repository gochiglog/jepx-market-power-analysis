"""設備対応の利用診断とheadroom準備状況を再現する（headroom計算なし）。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.asset_analysis_readiness import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    q = run(args.data_root, Path(__file__).resolve().parents[1], args.output)
    print(q["analysis_use_counts"], q["gap"])
