import csv
from decimal import Decimal
from pathlib import Path
from shutil import copyfile

import pytest

from src.unmatched_generation import (
    code_census,
    code_features,
    lexical,
    plant_stem,
    run,
    scenario_stats,
    score_pair,
    unit_token,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("川崎２ー３軸", "2-3"),
        ("川崎2-3号", "2-3"),
        ("姉崎新１号", "新1"),
        ("７－２GTA", "7-2GTA"),
        ("第Ⅲ号機", "3"),
        ("単独", None),
    ],
)
def test_diagnostic_unit_tokens_preserve_meaningful_new_and_component_labels(raw, expected):
    assert unit_token(raw) == expected
    assert lexical("タービン") == "タービン"


def source(name="川崎火力発電所２号系列３軸", unit="川崎２ー３軸", code="GB573", area="東京"):
    return dict(plant_name_raw=name, unit_name_raw=unit, plant_code=code, area=area)


def test_cross_code_name_number_candidate_is_never_official_or_exact_mapping():
    g = source()
    h = source("川崎火力発電所", "川崎２－３号", "34054")
    before = dict(g)
    f = score_pair(g, h)
    assert f["C_strong_candidate"] and not f["B_exact_candidate"]
    assert not f["code_equal"] and f["code_mismatch_requires_review"]
    assert g == before and plant_stem(g["plant_name_raw"]) == "川崎"


def test_new_old_gt_components_and_different_areas_are_not_silently_matched():
    assert not score_pair(
        source("姉崎火力発電所新１号機", "姉崎新1号"), source("姉崎火力発電所", "姉崎1号")
    )["C_strong_candidate"]
    assert not score_pair(
        source("西名古屋火力発電所", "7-2号機"), source("西名古屋火力発電所", "7-2GTA")
    )["C_strong_candidate"]
    assert not score_pair(source(), source(area="中部"))["C_strong_candidate"]


def test_code_or_fuzzy_similarity_alone_never_qualifies_for_C():
    f = score_pair(source("東西発電所", "1号機"), source("東南発電所", "1号機"))
    assert f["code_equal"] and f["similarity"] > 0
    assert not f["C_strong_candidate"]
    assert code_features("", "")["code_equal"] is False
    assert code_features("ABC01", "ABC02")["common_prefix"] == "ABC0"
    assert code_features("ABC01", "XYZ01")["common_suffix"] == "01"


def test_code_census_keeps_code_and_name_cardinality_separate():
    g = {
        "a": dict(source(code="ABC01"), plant_name_normalized="a"),
        "b": dict(source(code="ABC01"), plant_name_normalized="b"),
    }
    h = [
        dict(source(code="ABC01"), plant_name_normalized="a"),
        dict(source(code="ABC01"), plant_name_normalized="b"),
    ]
    rows, q = code_census(g, h)
    assert q["generation_area_code_count"] == 1 and q["exact_area_code_count"] == 1
    assert rows[0]["generation_plant_label_count"] == 2 and rows[0]["hjks_unit_rows"] == 2
    assert not rows[0]["official_crosswalk_verified"]


def test_scenarios_are_monotonic_keep_ambiguity_and_do_not_duplicate_volume():
    gen = {}
    for i in range(3):
        gen[str(i)] = {
            **source(f"plant{i}", "unit", str(i)),
            "plant_name_normalized": f"plant{i}",
            "generation_sum": Decimal((i + 1) * 100),
            "plant_match": i == 0,
            "plant_ambiguous": False,
        }
    pairs = [
        {
            "asset_key_candidate": "1",
            "hjks_area": "東京",
            "hjks_plant_code": "x",
            "B_exact_candidate": True,
            "C_strong_candidate": True,
        },
        {
            "asset_key_candidate": "2",
            "hjks_area": "東京",
            "hjks_plant_code": "y",
            "B_exact_candidate": False,
            "C_strong_candidate": True,
        },
    ]
    q, _ = scenario_stats(gen, pairs + pairs)
    assert [Decimal(s["matched_generation_kwh"]) for s in q] == [100, 300, 600]
    assert not any(s["final_mapping_applied"] for s in q)
    assert gen["2"]["plant_match"] is False
    pairs.append(dict(pairs[-1], hjks_plant_code="z"))
    q, _ = scenario_stats(gen, pairs)
    assert q[-1]["ambiguous_plant_label_count"] == 1
    assert Decimal(q[-1]["matched_generation_kwh"]) == 300


def test_real_unmatched_diagnosis_reconciles_all_dimensions_and_preserves_inputs(tmp_path):
    checkout = Path(__file__).resolve().parents[1]
    root = checkout.parents[1] if checkout.parent.name == ".worktrees" else checkout
    source_qa = checkout / "reports/generation_hjks_FY2025_qa.json"
    if not source_qa.exists() or not (root / "data/raw/occto/generation").exists():
        pytest.skip("研究raw/Issue15診断のない環境")
    (tmp_path / "reports").mkdir()
    for name in ("generation_hjks_FY2025_qa.json", "generation_hjks_677_mapping_candidates.csv"):
        copyfile(checkout / "reports" / name, tmp_path / "reports" / name)
    q = run(root, tmp_path)
    assert Decimal(q["unmatched_generation_kwh"]) == 127705401000
    assert (
        q["unmatched_plant_label_count"] == 67 and q["unmatched_positive_plant_label_count"] == 63
    )
    assert q["positive_unmatched_code_count"] == 17
    assert Decimal(q["scenarios"][0]["matched_generation_kwh"]) == 518800283424
    assert q["scenarios"][1]["added_plant_label_count"] == 0
    assert q["scenarios"][2]["added_plant_label_count"] == 66
    assert Decimal(q["scenarios"][2]["generation_coverage"]) == 1
    assert (
        q["C_requiring_exact_code_also"]["matched_generation_kwh"]
        == q["scenarios"][0]["matched_generation_kwh"]
    )
    assert (
        q["raw_unchanged"] and q["generation_interim_unchanged"] and not q["final_mapping_applied"]
    )
    for dimension in ("area", "month", "code", "fuel", "diagnostic_cluster"):
        with (tmp_path / f"reports/unmatched_generation_by_{dimension}_20261016.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert sum(Decimal(r["unmatched_generation_kwh"]) for r in rows) == 127705401000
        assert sum(Decimal(r["total_observed_generation_kwh"]) for r in rows) == 646505684424
    with (tmp_path / "reports/unmatched_plants_20261016.csv").open() as f:
        plants = list(csv.DictReader(f))
    assert len(plants) == 67
    assert all(r["final_mapping"] == "False" for r in plants)
    assert sum(Decimal(r["generation_kwh_FY2025"]) for r in plants) == 127705401000
    with (tmp_path / "reports/additional_mapping_candidates_20261016.csv").open() as f:
        pairs = list(csv.DictReader(f))
    assert all(
        r["review_status"] == "manual_review" and r["final_mapping"] == "False" for r in pairs
    )
    categories = q["unit_unmatched_categories"]
    assert categories["plant_matched_unit_unmatched"]["unit_count"] == 67
    assert categories["plant_and_unit_unmatched"]["unit_count"] == 71
