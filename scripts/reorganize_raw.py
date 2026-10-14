"""承認済み棚卸しのパス対応を実行する。元バイト列を編集せず移動記録を残す。"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.raw_acquisition import append_manifest, now, read_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def confined(root, name, target=False):
    path = (root / name).resolve()
    base = root.resolve() / "data" / "raw" if target else root.resolve() / "data"
    if not path.is_relative_to(base):
        raise ValueError(f"保存領域外のパス: {name}")
    return path


def reorganize(root, inventory, execute=False):
    root = Path(root)
    records = read_manifest(root / "metadata/acquisition_manifest.jsonl")
    jobs = [r for r in inventory["files"] if r.get("recommended_path")]
    targets = set()
    # 1件でも不整合があれば移動前に停止する。再実行は移動済みSHAで判定する。
    for r in jobs:
        source = confined(root, r["current_path"])
        target = confined(root, r["recommended_path"], target=True)
        if target in targets:
            raise ValueError("推奨保存先の衝突")
        targets.add(target)
        if source.exists():
            if digest(source) != r["sha256"]:
                raise ValueError(f"移動前SHA256不一致: {source}")
            if source != target and target.exists():
                raise FileExistsError(target)
        elif not target.exists() or digest(target) != r["sha256"]:
            raise ValueError(f"移動元/移動先を確認できない: {source}")
    if not execute:
        return {"planned_count": len(jobs), "executed": False}
    mapping = {}
    for r in jobs:
        source = confined(root, r["current_path"])
        target = confined(root, r["recommended_path"], target=True)
        if source.exists() and source != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            # hard linkの排他的作成後に元名だけ外す。上書きせず同じinodeを移す。
            os.link(source, target)
            if digest(target) != r["sha256"]:
                raise ValueError("移動先SHA256不一致。元ファイルは保持")
            source.unlink()
            append_manifest(
                root / "metadata/raw_relocation_manifest.jsonl",
                {
                    "moved_at": now(),
                    "original_path": r["current_path"],
                    "original_filename": r["original_filename"],
                    "local_path": r["recommended_path"],
                    "sha256": r["sha256"],
                    "file_size": r["file_size"],
                    "approval": "ユーザー明示承認 2026-10-14",
                },
            )
        if digest(target) != r["sha256"]:
            raise ValueError("移動後SHA256不一致")
        mapping[r["current_path"]] = r["recommended_path"]
    for r in records:
        if r.get("local_path") in mapping:
            r.setdefault("original_local_path", r["local_path"])
            r["local_path"] = mapping[r["local_path"]]
    manifest = root / "metadata/acquisition_manifest.jsonl"
    temporary = manifest.with_suffix(".migration.tmp")
    temporary.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    temporary.replace(manifest)
    return {"verified_count": len(jobs), "executed": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            reorganize(ROOT, json.loads(args.inventory.read_text()), args.execute),
            ensure_ascii=False,
        )
    )
