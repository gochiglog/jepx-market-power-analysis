"""固定Parquetの型・列・出典を読む。研究データは出力しない。"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_browser import column_catalog, table_registry  # noqa: E402


def main():
    root = Path(__file__).resolve().parents[1]
    tables = table_registry(root)
    columns = []
    for table in tables:
        columns.extend(column_catalog(root / table["source_path"], root, table["table_id"]))
    target = root / "metadata/view_table_registry.json"
    target.write_text(
        json.dumps(
            {"purpose": "読み取り専用の閲覧層。最終baselineではない。", "tables": tables},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    with (root / "metadata/view_column_dictionary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(columns)
    print(f"{len(tables)}表・{len(columns)}列の対応表を作成")


if __name__ == "__main__":
    main()
