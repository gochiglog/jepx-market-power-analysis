"""参照容量と30分平均発電出力の差を診断する。停止補正はしない。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.reference_capacity_gap import run, summarize_result  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    q = run(a.data_root, Path(__file__).resolve().parents[1], a.output)
    q = summarize_result(Path(__file__).resolve().parents[1])
    print(q["mapping_route_use_counts"], q["calculable_cells"], q["negative_gap_cells"])
