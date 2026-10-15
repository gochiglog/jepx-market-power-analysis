"""手動収集の未知URL/取得時刻と不正なmanifestを検証する。"""

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.register_manual_raw import register
from src.raw_acquisition import read_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_manual_import_keeps_unknown_provenance_and_does_not_change_bytes(tmp_path):
    data = "受渡日,時刻コード\n2025/04/01,1\n".encode("cp932")
    p = tmp_path / "data/raw/jepx/spot/manual.csv"
    p.parent.mkdir(parents=True)
    p.write_bytes(data)
    inventory = {
        "files": [
            {
                "recommended_path": str(p.relative_to(tmp_path)),
                "current_path": "data/manual.csv",
                "original_filename": "manual.csv",
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_estimate": "jepx_spot",
                "date_column": "受渡日",
            }
        ]
    }
    assert register(tmp_path, inventory) == 1
    r = read_manifest(tmp_path / "metadata/acquisition_manifest.jsonl")[0]
    assert r["source_url"] is None and r["downloaded_at"] is None
    assert r["registered_at"]
    schema = json.loads((ROOT / "metadata/acquisition_manifest.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(r)
    assert p.read_bytes() == data
    assert register(tmp_path, inventory) == 0
    r["status"] = "downloaded"
    with pytest.raises(ValidationError):
        validator.validate(r)


def test_approved_deletion_stays_in_manifest_without_being_a_missing_raw(tmp_path):
    from scripts.audit_raw import audit

    data = "受渡日,時刻コード\n2025/04/01,1\n".encode("cp932")
    path = tmp_path / "data/raw/source/manual.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    inventory = {
        "files": [
            {
                "recommended_path": str(path.relative_to(tmp_path)),
                "current_path": "data/manual.csv",
                "original_filename": "manual.csv",
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_estimate": "jepx_spot",
                "date_column": "受渡日",
            }
        ]
    }
    register(tmp_path, inventory)
    manifest = tmp_path / "metadata/acquisition_manifest.jsonl"
    record = read_manifest(manifest)[0]
    record.update(
        status="deleted",
        previous_status="imported",
        deleted_at=record["registered_at"],
        deletion_reason="人工fixtureの明示削除",
        deletion_approval="test approval",
    )
    path.unlink()
    manifest.write_text(json.dumps(record) + "\n")
    (tmp_path / "metadata/data_sources.yml").write_text("sources:\n- source_id: jepx_spot\n")
    (tmp_path / "metadata/acquisition_manifest.schema.json").write_bytes(
        (ROOT / "metadata/acquisition_manifest.schema.json").read_bytes()
    )
    result = audit(tmp_path)
    assert result["registered_file_count"] == 0
    assert result["unregistered_raw_paths"] == []
    assert result["approved_deletions"][0]["sha256"] == record["sha256"]
