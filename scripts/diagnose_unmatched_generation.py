"""677行参照台帳の未対応発電量を分解し、追加mappingは候補としてのみ出す。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.unmatched_generation import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    q = run(args.data_root, Path(__file__).resolve().parents[1])
    print(q["unmatched_generation_kwh"], q["scenarios"])
