"""FY2025基本市場パネルと既存連系線を接続する。固定版を上書きしない。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.market_panel import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    qa = run(args.data_root, Path(__file__).resolve().parents[1], args.output)
    print(qa["base"]["rows"], len(qa["profiles"]))
