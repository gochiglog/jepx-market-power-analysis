"""感度診断が正式対応や重複加算に変わらないことを検査。"""

import csv
import json
from decimal import Decimal
from pathlib import Path
from shutil import copyfile

import pytest

from src.official_asset_review import coverage, run


def asset(key, amount, matched=False, plant="plant", area="東京"):
    return dict(
        asset_key_candidate=key,
        area=area,
        plant_code=plant,
        plant_name_normalized=plant,
        generation_sum=str(amount),
        plant_match=str(matched),
        unit_match=str(matched),
    )


def test_supported_candidates_do_not_change_confirmed_coverage():
    assets = [asset("a", 100, True), asset("b", 300, plant="other")]
    evidence = [
        dict(
            asset_key_candidate="b",
            hjks_snapshot_row_id="right",
            verification_status="strongly_supported",
        )
    ]
    assert Decimal(coverage(assets, evidence, {"confirmed"})["generation_coverage"]) == Decimal(
        ".25"
    )
    q = coverage(assets, evidence, {"confirmed", "strongly_supported"})
    assert Decimal(q["generation_coverage"]) == 1
    assert q["matched_unit_count"] == 2 and not q["final_mapping_applied"]


def test_ambiguous_and_repeated_evidence_never_inflate_quantity():
    assets = [asset("a", 100), asset("b", 300, plant="other")]
    e = dict(asset_key_candidate="a", hjks_snapshot_row_id="r1", verification_status="confirmed")
    assert Decimal(coverage(assets, [e, e], {"confirmed"})["matched_generation_kwh"]) == 100
    q = coverage(assets, [e, dict(e, hjks_snapshot_row_id="r2")], {"confirmed"})
    assert q["matched_unit_count"] == 0 and Decimal(q["matched_generation_kwh"]) == 0


def test_partial_plant_is_not_counted_as_full_plant():
    assets = [asset("a", 100), asset("b", 300)]
    e = dict(asset_key_candidate="a", hjks_snapshot_row_id="r1", verification_status="confirmed")
    q = coverage(assets, [e], {"confirmed"})
    assert q["matched_plant_count"] == 0 and q["matched_unit_count"] == 1


def test_real_official_evidence_and_reference_hashes(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    root = repo.parents[1] if repo.parent.name == ".worktrees" else repo
    manifest = repo / "metadata/research/asset_reference_manifest_20261016.json"
    refs = json.loads(manifest.read_text())
    if not all((root / r["local_path"]).exists() for r in refs):
        pytest.skip("公式参考原本を保有する環境のみ")
    (tmp_path / "reports").mkdir()
    (tmp_path / "metadata/research").mkdir(parents=True)
    copyfile(manifest, tmp_path / manifest.relative_to(repo))
    names = [
        "generation_hjks_FY2025_qa.json",
        "generation_hjks_677_mapping_candidates.csv",
        "additional_mapping_candidates_20261016.csv",
    ]
    before = {n: (repo / "reports" / n).read_bytes() for n in names}
    for n in names:
        copyfile(repo / "reports" / n, tmp_path / "reports" / n)
    q = run(root, tmp_path)
    assert q["confirmed"] == 0 and q["supported_source_unit_count"] == 49
    assert q["supported_code_count"] == 10 and not q["missing_from_677"]
    assert all(n == 0 for n in q["code_search_counts"].values())
    a, b, c = (q["scenarios"][k] for k in ("A_strict", "B_confirmed", "C_sensitivity"))
    assert a == b and Decimal(a["matched_generation_kwh"]) == 518800283424
    assert Decimal(c["matched_generation_kwh"]) == 630814437424
    assert c["matched_plant_count"] == 345 and c["matched_unit_count"] == 402
    assert Decimal(c["unmatched_generation_kwh"]) == 15691247000
    assert not q["existing_mapping_modified"] and q["existing_generation_raw_sha_unchanged"]
    saved = json.loads((repo / "reports/top10_official_review_qa_20261016.json").read_text())
    assert saved == q
    with (tmp_path / "reports/top10_official_mapping_evidence_20261016.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert all(r["direct_code_crosswalk_verified"] == "False" for r in rows)
    assert all(r["final_mapping"] == "False" for r in rows)
    assert all((repo / "reports" / n).read_bytes() == before[n] for n in names)
