import csv
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.investigate_price_discrepancy import quantile, run


def test_decimal_quantile_does_not_round_original_prices():
    assert Decimal(quantile([Decimal(".01"), Decimal(".02")], ".5")) == Decimal(".015")


def test_real_discrepancy_diagnostics_keep_all_keys_and_original_c(tmp_path):
    checkout = Path(__file__).resolve().parents[1]
    root = checkout.parents[1] if checkout.parent.name == ".worktrees" else checkout
    if not (root / "data/raw/jepx/spot/spot_summary_2025.csv").exists():
        pytest.skip("研究rawがないCI")
    (tmp_path / "reports").mkdir()
    report = run(root, tmp_path)
    assert report["mismatch_count"] == 2271
    assert report["first_mismatch"] == ("2025-04-02", 28)
    assert report["last_mismatch"] == ("2026-03-02", 46)
    assert report["last_consistent_run"]["consecutive_periods"] == 1394
    assert report["longest_mismatch_run"]["consecutive_periods"] == 26
    assert not report["all_48_mismatch_days"]
    assert sum(r["mismatch_count"] for r in report["monthly"]) == 2271
    assert report["matches_any_area"] == 211
    assert report["raw_unchanged"] and not report["processed_written"]
    with (tmp_path / "reports/price_discrepancy_details.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2271
    for row in rows:
        a, b, c = (
            Decimal(row[n])
            for n in (
                "spot_system_price_A",
                "sensitivity_system_price_B",
                "difference_file_system_price_C_raw",
            )
        )
        assert c == 0
        assert a - b == Decimal(row["A_minus_B"])
        assert b - c == Decimal(row["B_minus_C_raw"])
        assert abs(a - b) / abs(a) == Decimal(row["relative_absolute_difference_to_A"])
    with (tmp_path / "reports/price_discrepancy_offset_tests.csv").open() as stream:
        offsets = list(csv.DictReader(stream))
    for row in offsets:
        total = 17520 if row["cohort"] == "all" else 2271
        assert int(row["comparison_count"]) + int(row["unmatched_keys"]) == total
        if row["cohort"] == "original_mismatches":
            assert int(row["matched"]) < int(row["comparison_count"])
    assert not list(tmp_path.rglob("*.parquet"))
