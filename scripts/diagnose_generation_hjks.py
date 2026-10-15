"""Issue #15のlong化・全版比較・非確定mapping診断を実行する。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.generation_hjks import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    qa = run(args.data_root, Path(__file__).resolve().parents[1])
    print(qa["status"], qa["generation"]["long_rows"])
