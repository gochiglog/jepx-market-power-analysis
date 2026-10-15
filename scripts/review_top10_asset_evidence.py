"""保存済み公式参考資料と既存候補を用い、正式対応せずcoverageを診断する。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.official_asset_review import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    q = run(args.data_root, Path(__file__).resolve().parents[1])
    print(q["evidence_pair_counts"], q["scenarios"])
