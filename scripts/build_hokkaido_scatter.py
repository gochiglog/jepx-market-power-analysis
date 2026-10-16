"""北海道の参照群と第一版記述図を作成する。"""

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/jepx_hokkaido_matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.hokkaido_scatter import run  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parents[1]
    parser.add_argument("--data-root", type=Path, default=repo)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-dir", type=Path, help="再実行時は固定版と別の新規フォルダ")
    args = parser.parse_args()
    output = (
        args.output or args.data_root / "data/processed/description/hokkaido/20261016_scatter_v1"
    )
    result = run(args.data_root, repo, output, report=args.report_dir)
    print(f"北海道 {result['rows']:,}コマ、参照群{result['scope_unit_count']}unit。{output}")


if __name__ == "__main__":
    main()
