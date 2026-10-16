"""研究判断準備の比較表と既存設備候補の事業者根拠を整備する。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.decision_preparation import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.data_root.resolve(), Path(__file__).resolve().parents[1], args.output.resolve())
