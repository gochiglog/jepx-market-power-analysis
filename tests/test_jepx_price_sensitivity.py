import csv
import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.jepx_price_sensitivity import (
    DIFFERENCE_COLUMNS,
    SCENARIO_COLUMNS,
    original_rows,
    run,
    transform,
)
from src.jepx_spot import NUMERIC_COLUMNS, choose_canonical
from src.price_sensitivity_audit import SCENARIOS, select_canonical
from src.time_normalization import sha256


def encode(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


@pytest.fixture
def sample(monkeypatch):
    # 48コマの合成1日を、年度全日検査に供する日付集合として注入する。
    dates = lambda year: [date(2025, 4, 1)]  # noqa: E731
    monkeypatch.setattr("src.price_sensitivity_audit.fiscal_dates", dates)
    monkeypatch.setattr("src.jepx_spot.fiscal_dates", dates)
    actual, difference, spot = [], [], []
    for period in range(1, 49):
        common = {"年月日": "2025/04/01", "時刻コード": str(period)}
        actual.append({**common, "システムプライス": "9.03", **{c: "8.00" for c in SCENARIOS}})
        difference.append({**common, "システムプライス": "0.00", **{c: "-1.03" for c in SCENARIOS}})
        spot.append(
            {
                "受渡日": "2025/04/01",
                "時刻コード": str(period),
                **{v: "9.07" if "price" in k else "100" for k, v in NUMERIC_COLUMNS.items()},
            }
        )
    return actual, difference, spot


def convert(sample):
    return transform(
        *(encode(rows) for rows in sample),
        {
            "sensitivity": "a",
            "sensitivity_diff": "b",
            "spot": "c",
        },
    )


def test_mismatch_is_known_qa_and_never_substitutes_baseline(sample):
    table, baseline, qa = convert(sample)
    assert qa["all_passed"] and qa["status"] == "completed_with_known_qa_issue"
    assert qa["comparison"]["spot_price_mismatch_count"] == 48
    assert (
        qa["comparison"]["candidate_formula_match_counts"]["virtual_minus_sensitivity_base"] == 288
    )
    assert not qa["known_qa_issues"][0]["blocking"]
    assert not qa["difference_formula_officially_verified"]
    assert table["system_price_sensitivity_base"][0].as_py() == Decimal("9.03")
    assert table["published_difference_sell_500mw"][0].as_py() == Decimal("-1.03")
    assert "system_price_market" not in table.column_names
    assert baseline[0]["system_price_market"] == "9.07"
    assert baseline[0]["price_baseline_difference"] == "-0.04"
    assert not baseline[0]["baseline_price_match_flag"]
    assert str(table["timestamp_start"][0].as_py()) == "2025-04-01 00:00:00+09:00"
    assert str(table["timestamp_end"][-1].as_py()) == "2025-04-02 00:00:00+09:00"
    assert table["difference_raw__システムプライス"][0].as_py() == "0.00"


def test_different_difference_raw_row_order_keeps_original_cells_and_line_numbers(sample):
    sample[1].reverse()
    table, _, qa = convert(sample)
    assert qa["all_passed"]
    assert table["source_difference_row_number"].to_pylist() == list(range(49, 1, -1))
    assert table["difference_raw__時刻コード"].to_pylist() == [str(p) for p in range(1, 49)]


@pytest.mark.parametrize("value", ["未確定", "NaN", "1,000", ""])
def test_parse_or_null_anomaly_is_reported_without_processed(sample, value):
    sample[0][0]["売500MW"] = value
    table, _, qa = convert(sample)
    assert table is None and not qa["all_passed"]
    assert qa["checks"]["actual"]["nulls"] or qa["checks"]["actual"]["numeric_parse_failures"]


def test_published_difference_disagreement_blocks_output_without_recalculation(sample):
    sample[1][0]["売500MW"] = "-1.07"
    table, _, qa = convert(sample)
    assert table is None and qa["status"] == "blocked_internal_difference_qa"
    assert len(qa["comparison"]["candidate_formula_mismatches"]) == 1
    assert sample[1][0]["売500MW"] == "-1.07"


def test_missing_period_blocks_output(sample):
    sample[1].pop()
    table, _, qa = convert(sample)
    assert table is None and not qa["checks"]["difference"]["time_qa"]["passed"]


def test_duplicate_key_raises_without_dropping(sample):
    sample[0][1]["時刻コード"] = "1"
    with pytest.raises(ValueError, match="重複"):
        convert(sample)


def test_real_fy2025_etl_keeps_raw_cells_all_keys_and_published_values(tmp_path):
    checkout = Path(__file__).resolve().parents[1]
    root = checkout.parents[1] if checkout.parent.name == ".worktrees" else checkout
    if not (root / "data/raw/jepx/price_sensitivity/virtualprice_2025.csv").exists():
        pytest.skip("研究rawのない環境")
    records = [
        json.loads(x)
        for x in (root / "metadata/acquisition_manifest.jsonl").read_text().splitlines()
    ]
    chosen = {}
    selected = []
    for variant in ("sensitivity", "sensitivity_diff", "spot"):
        record, copies = (
            choose_canonical(records, 2025)
            if variant == "spot"
            else select_canonical(records, variant)
        )
        chosen[variant] = record
        selected.extend([record, *copies])
    manifest = tmp_path / "metadata/acquisition_manifest.jsonl"
    manifest.parent.mkdir()
    manifest.write_text("\n".join(json.dumps(r) for r in selected))
    for record in selected:
        path = tmp_path / record["local_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / record["local_path"]).read_bytes())
    qa = run(tmp_path, tmp_path)
    table = pq.read_table(qa["output_path"])
    assert table.num_rows == 17520 and len(set(table["delivery_date"].to_pylist())) == 365
    assert table.num_columns == 40
    assert qa["parquet_roundtrip_verified"] and qa["raw_unchanged"] and qa["all_passed"]
    assert all(
        q["time_qa"]["passed"] and q["time_qa"]["duplicate_keys"] == 0
        for q in qa["checks"].values()
    )
    assert not any(qa["numeric_null_counts"].values())
    assert qa["comparison"]["key_match"]["actual_match_rate"] == 1
    assert (
        qa["comparison"]["candidate_formula_match_counts"]["virtual_minus_sensitivity_base"]
        == 105120
    )
    assert qa["comparison"]["spot_price_match_count"] == 15249
    assert qa["comparison"]["spot_price_mismatch_count"] == 2271
    assert round(100 * qa["comparison"]["spot_price_match_rate"], 2) == 87.04
    assert str(table["delivery_date"][-1].as_py()) == "2026-03-31"
    assert table["timestamp_end"][-1].as_py().isoformat() == "2026-04-01T00:00:00+09:00"
    with Path(qa["baseline_qa_path"]).open() as stream:
        baseline = list(csv.DictReader(stream))
    assert len(baseline) == 17520
    assert sum(r["baseline_price_match_flag"] == "True" for r in baseline) == 15249
    for variant, prefix in (("sensitivity", ""), ("sensitivity_diff", "difference_raw__")):
        record = chosen[variant]
        rows = original_rows((root / record["local_path"]).read_bytes())
        for name in rows[0]:
            assert table[f"{prefix}{name}"].to_pylist() == [r[name] for r in rows]
        mapping = SCENARIO_COLUMNS if variant == "sensitivity" else DIFFERENCE_COLUMNS
        for name, raw_column in mapping.items():
            assert table[name].to_pylist() == [Decimal(r[raw_column]) for r in rows]
    for record in selected:
        assert sha256(root / record["local_path"]) == record["sha256"]
        assert sha256(tmp_path / record["local_path"]) == record["sha256"]
    with pytest.raises(FileExistsError):
        run(tmp_path, tmp_path)
