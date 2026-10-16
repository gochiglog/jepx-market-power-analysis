"""元頻度需要・計画需給ETLと方向対応の別版を構築する。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.demand_supply import run  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    qa = run(a.data_root, Path(__file__).resolve().parents[1], a.output)
    print(qa["demand"]["source_rows"], [m["rows"] for m in qa["market_connections"]])
