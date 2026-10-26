"""計算途中版を保持し、元unit列の宣言型を保つ採用候補版へ固定する。"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pyarrow.parquet as pq


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(root, source, destination):
    if destination.exists():
        raise ValueError("既存版を上書きしない")
    qa = root / "reports/21_adjustable_removal/qa/run_audit.json"
    audit = json.loads(qa.read_text())
    destination.mkdir(parents=True)
    outputs = []
    inputs = []
    for entry in audit["outputs"]:
        old = root / entry["path"]
        if sha(old) != entry["sha256"]:
            raise ValueError("途中版SHA不一致")
        relative = old.relative_to(source)
        new = destination / relative
        new.parent.mkdir(parents=True, exist_ok=True)
        if relative.parts[0] == "unit_trial_uppers":
            name = old.stem.removesuffix("__record_held_reference")
            original = (
                root
                / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1/unit_scenarios"
                / (name + ".parquet")
            )
            declared = pq.read_schema(original)
            table = pq.read_table(old)
            before = table
            for field in declared:
                i = table.column_names.index(field.name)
                table = table.set_column(i, field, table.column(i).cast(field.type, safe=True))
            for name in before.column_names:
                # 元数値・文字列を変更しない安全cast。型だけを元宣言へ戻す。
                assert before[name].cast(table[name].type, safe=True).equals(table[name])
            pq.write_table(table, new, compression="zstd")
            assert pq.read_table(new).equals(table)
        else:
            shutil.copy2(old, new)
            assert sha(old) == sha(new)
        outputs.append(
            {"path": str(new.relative_to(root)), "sha256": sha(new), "rows": entry["rows"]}
        )
        inputs.append({"path": entry["path"], "sha256": entry["sha256"]})
    audit.update(
        {
            "version": destination.name,
            "computation_draft_version": source.name,
            "normalization": "元unit宣言型への安全castのみ。数値・文字列・元行を保持",
            "outputs": outputs,
            "build_inputs": inputs,
        }
    )
    qa.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    (root / "metadata/adjustable_removal_manifest_20261027.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    print("frozen", len(outputs), "files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(Path(__file__).resolve().parents[1], args.input.resolve(), args.output.resolve())
