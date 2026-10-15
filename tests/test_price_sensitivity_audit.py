from decimal import Decimal
from pathlib import Path

import pytest

from src.price_sensitivity_audit import SCENARIOS, compare, run, select_canonical


def test_sensitivity_base_and_spot_price_are_not_substituted():
    key = ("2025-04-02", 28)
    actual = {key: {"システムプライス": Decimal("9.03"), **{c: Decimal("8.00") for c in SCENARIOS}}}
    difference = {
        key: {"システムプライス": Decimal("0.00"), **{c: Decimal("-1.03") for c in SCENARIOS}}
    }
    result = compare(actual, difference, {key: Decimal("9.07")})
    assert result["spot_price_mismatch_count"] == 1
    assert result["candidate_formula_match_counts"]["virtual_minus_sensitivity_base"] == 6
    assert result["candidate_formula_match_counts"]["sensitivity_base_minus_virtual"] == 0
    assert actual[key]["システムプライス"] == Decimal("9.03")
    assert result["spot_price_mismatches"][0]["difference_sensitivity_minus_spot"] == "-0.04"
    assert result["key_match"]["actual_match_rate"] == 1
    assert not result["processed_written"] and not result["formula_officially_verified"]


def test_different_sha_candidates_block_selection():
    base = {
        "source_id": "jepx_price_sensitivity",
        "status": "downloaded",
        "variant": "sensitivity",
        "period_start": "2025-04-01",
        "period_end": "2026-03-31",
        "sha256": "a",
        "original_filename": "virtualprice.csv",
    }
    manual = dict(base, status="imported", variant="manual", sha256="b")
    with pytest.raises(ValueError, match="ユーザー確認"):
        select_canonical([base, manual], "sensitivity")


def test_real_fy2025_preflight_does_not_write_processed(tmp_path):
    checkout = Path(__file__).resolve().parents[1]
    root = checkout.parents[1] if checkout.parent.name == ".worktrees" else checkout
    spot = root / "data/processed/jepx/spot/jepx_spot_FY2025.parquet"
    if not spot.exists():
        pytest.skip("研究raw/spotのないCI")
    (tmp_path / "reports").mkdir()
    result = run(root, tmp_path)
    assert result["scenario_comparisons"] == 105120
    assert result["candidate_formula_match_counts"]["virtual_minus_sensitivity_base"] == 105120
    assert result["spot_price_mismatch_count"] == 2271
    assert result["key_match"]["actual_rows"] == 17520
    assert result["key_match"]["difference_unmatched"] == 0
    assert all(
        c["time_qa"]["days"] == 365
        and c["time_qa"]["passed"]
        and not c["nulls"]
        and not c["numeric_parse_failures"]
        for c in result["checks"].values()
    )
    assert result["raw_unchanged"]
    assert result["status"] == "preflight_only_no_output"
    assert not list(tmp_path.rglob("*.parquet"))
