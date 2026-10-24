"""案Aのsnapshot公表記録付与診断版を構築する。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.public_record_annotation import run  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    q = run(a.data_root, Path(__file__).resolve().parents[1], a.output)
    print(q["original_rows"], q["market_join_rows"])
