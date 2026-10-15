import csv
import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "metadata/research"


def read(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_targets_have_market_area_period_evidence_without_flags():
    rows = read("regulatory_dominant_targets.csv")
    assert len(rows) == 11 and len({r["regulatory_entity"] for r in rows}) == 10
    keys = [(r["regulatory_entity"], r["market_area"], r["valid_from"]) for r in rows]
    assert len(set(keys)) == len(keys)
    for row in rows:
        assert date.fromisoformat(row["valid_from"]) <= date(2025, 4, 1)
        assert date.fromisoformat(row["valid_to"]) >= date(2026, 3, 31)
        assert row["market"] == "JEPXスポット"
        assert row["source_url"].startswith("https://www.egc.meti.go.jp/")
        assert row["source_page"] and row["decision_basis"]
        assert row["verification_status"] == "official_search_verified"
        assert row["raw_pdf_verified"] == "false"
        assert "dominant_flag" not in row and "fringe_flag" not in row
    assert "中部電力株式会社" not in {r["regulatory_entity"] for r in rows}


def test_names_not_corporate_groups_determine_candidate_and_no_final_flag():
    rows = read("operator_mapping_candidates.csv")
    targets = {
        (r["regulatory_entity"], r["market_area"]) for r in read("regulatory_dominant_targets.csv")
    }
    assert len(rows) == len({(r["operator_raw"], r["area_raw"]) for r in rows}) == 105
    for row in rows:
        assert row["mapping_confidence"] in {
            "exact",
            "normalized_exact",
            "manual_verified",
            "manual_review",
            "unresolved",
        }
        assert row["regulatory_eligibility_status"] == "pending_research_approval"
        assert "dominant_flag" not in row and "fringe_flag" not in row
        if row["regulatory_entity"]:
            assert (row["regulatory_entity"], row["area_raw"]) in targets
        if row["name_match_type"] == "different_legal_entity_review":
            assert row["mapping_confidence"] == "manual_review"
            assert row["regulatory_entity"] == ""
            assert row["corporate_evidence_url"]
        if row["corporate_group_confidence"] == "manual_verified":
            assert row["corporate_evidence_url"] and row["group_evidence_as_of"]


def test_coverage_and_unresolved_account_for_every_pair():
    rows = read("operator_mapping_candidates.csv")
    unresolved = read("operator_mapping_unresolved.csv")
    matched = [r for r in rows if r["mapping_confidence"] in ("exact", "normalized_exact")]
    stats = json.loads((ROOT / "operator_mapping_coverage.json").read_text())
    assert len(matched) == stats["matched_hjks_pairs"] == 9
    assert len(unresolved) == stats["nonmatched_hjks_pairs"] == 96
    assert len(matched) + len(unresolved) == len(rows)
    assert not stats["dominant_flag_assigned"] and not stats["fringe_flag_assigned"]
    documents = yaml.safe_load((ROOT / "issue_3_official_documents.yml").read_text())
    assert all(
        d["verification_status"] == "official_search_verified" and not d["raw_pdf_saved"]
        for d in documents["documents"]
    )


def test_phase_a_regulatory_flag_does_not_assign_asset_flags():
    targets = read("regulatory_targets.csv")
    assert len(targets) == 11
    assert all(
        r["regulatory_target_flag"] == "1" and r["verification_level"] == "official_search_verified"
        for r in targets
    )
    assets = read("asset_regulatory_mapping_candidates.csv")
    assert len(assets) == 1206
    keys = {(r["source_sha256"], r["source_row_number"]) for r in assets}
    assert len(keys) == len(assets)
    for row in assets:
        assert row["mapping_status"] in ("manual_review", "unresolved")
        assert not row["valid_from"] and not row["valid_to"]
        assert "dominant_asset_flag" not in row and "fringe_flag" not in row
        if row["operator_raw"].startswith("東京電力") or row["operator_raw"] == "中部電力(株)":
            assert not row["regulatory_entity_candidate"]
