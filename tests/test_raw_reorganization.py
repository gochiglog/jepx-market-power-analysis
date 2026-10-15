"""承認済み整理の上書き防止・SHA検証・再実行・manifestの元名保持を検証する。"""

import hashlib
import json

import pytest

from scripts.reorganize_raw import reorganize


def setup_inventory(root):
    source = root / "data/raw/manual.csv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original bytes")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    record = {
        "current_path": "data/raw/manual.csv",
        "recommended_path": "data/raw/source/file.csv",
        "original_filename": "manual.csv",
        "sha256": sha,
        "file_size": 14,
    }
    (root / "metadata").mkdir()
    (root / "metadata/acquisition_manifest.jsonl").write_text(
        json.dumps(
            {
                "local_path": record["current_path"],
                "original_filename": "official.csv",
                "sha256": sha,
            }
        )
        + "\n"
    )
    return {"files": [record]}


def test_move_preserves_bytes_original_name_and_is_resumable(tmp_path):
    inventory = setup_inventory(tmp_path)
    assert not reorganize(tmp_path, inventory)["executed"]
    assert (tmp_path / "data/raw/manual.csv").exists()
    reorganize(tmp_path, inventory, True)
    assert not (tmp_path / "data/raw/manual.csv").exists()
    assert (tmp_path / "data/raw/source/file.csv").read_bytes() == b"original bytes"
    manifest = json.loads((tmp_path / "metadata/acquisition_manifest.jsonl").read_text())
    assert manifest["original_filename"] == "official.csv"
    assert manifest["original_local_path"] == "data/raw/manual.csv"
    assert manifest["local_path"] == "data/raw/source/file.csv"
    reorganize(tmp_path, inventory, True)
    assert len((tmp_path / "metadata/raw_relocation_manifest.jsonl").read_text().splitlines()) == 1


def test_collision_is_rejected_before_any_move(tmp_path):
    inventory = setup_inventory(tmp_path)
    target = tmp_path / inventory["files"][0]["recommended_path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"other")
    with pytest.raises(FileExistsError):
        reorganize(tmp_path, inventory, True)
    assert (tmp_path / "data/raw/manual.csv").read_bytes() == b"original bytes"
    assert target.read_bytes() == b"other"


def test_changed_source_is_rejected(tmp_path):
    inventory = setup_inventory(tmp_path)
    (tmp_path / "data/raw/manual.csv").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA256"):
        reorganize(tmp_path, inventory, True)


def test_target_outside_raw_is_rejected(tmp_path):
    inventory = setup_inventory(tmp_path)
    inventory["files"][0]["recommended_path"] = "../outside.csv"
    with pytest.raises(ValueError, match="保存領域外"):
        reorganize(tmp_path, inventory, True)


def test_resume_follows_later_version_correction(tmp_path):
    inventory = setup_inventory(tmp_path)
    reorganize(tmp_path, inventory, True)
    correction = {
        "files": [
            dict(
                inventory["files"][0],
                current_path="data/raw/source/file.csv",
                recommended_path="data/raw/updated/file.csv",
            )
        ]
    }
    reorganize(tmp_path, correction, True)
    reorganize(tmp_path, inventory, True)
    assert (tmp_path / "data/raw/updated/file.csv").read_bytes() == b"original bytes"
    assert not (tmp_path / "data/raw/source/file.csv").exists()
    assert len((tmp_path / "metadata/raw_relocation_manifest.jsonl").read_text().splitlines()) == 2
