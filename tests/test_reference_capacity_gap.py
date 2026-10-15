"""承認された単位換算と参照容量差の診断契約を検査する。"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from src.reference_capacity_gap import D, add_columns, micro, period_flags, scope_equal


def mrow():
    return dict(
        asset_key_candidate="a",
        operation_start_raw="1900/01/01",
        operation_end_raw="9999/12/31",
        research_use_status="use_diagnostic",
        capacity_reference_raw_kw="100",
        target_snapshot_row_id="sha:2",
        diagnostic_mapping_route="candidate49",
        official_verification_status="strongly_supported",
        research_use_reason="使用可（診断限定）",
        capacity_reference_source_file="snapshot.csv",
        capacity_reference_source_sha256="sha",
        capacity_snapshot_registered_at="2026-10-14",
        capacity_snapshot_downloaded_at=None,
        capacity_snapshot_download_time_known=False,
        capacity_constant_assumption=True,
        capacity_effective_period_verified=False,
        block_component_check="対象外",
    )


def batch(values, days=None):
    n = len(values)
    return pa.RecordBatch.from_arrays(
        [
            pa.array(["a"] * n),
            pa.array(days or [date(2025, 4, 1)] * n),
            pa.array(values, type=D),
            pa.array(list(range(1, n + 1))),
            pa.array(["原セルを保持"] * n),
        ],
        names=[
            "asset_key_candidate",
            "delivery_date",
            "generation_kwh_30min",
            "period",
            "original",
        ],
    )


def test_average_output_conversion_and_negative_gap_preserve_original_and_null():
    b = batch([Decimal(10), Decimal(60), None, Decimal("50.000001")])
    t, _ = add_columns(b, [mrow()])
    assert t["generation_avg_kw"].to_pylist() == [
        Decimal(20),
        Decimal(120),
        None,
        Decimal("100.000002"),
    ]
    assert t["capacity_gap_reference_kw"].to_pylist() == [
        Decimal(80),
        Decimal(-20),
        None,
        Decimal("-0.000002"),
    ]
    assert t["qa_negative_reference_gap"].to_pylist() == [False, True, False, True]
    assert t["generation_kwh_30min"].equals(pa.chunked_array([b.column(2)]))
    assert t["original"].to_pylist() == ["原セルを保持"] * 4
    assert all(v == "strongly_supported" for v in t["official_verification_status"].to_pylist())
    assert t["capacity_snapshot_downloaded_at"].null_count == 4
    assert micro(t["capacity_gap_reference_kw"]).tolist() == [80000000, -20000000, 0, -2]
    assert t["qa_generation_missing"].to_pylist() == [False, False, True, False]


def test_operation_dates_are_not_fabricated_capacity_effective_intervals():
    assert period_flags("2025-04-01", "1900/01/01", "9999/12/31") == (False, False)
    assert period_flags("2025-04-02", "1900/01/01", "2025/04/01") == (True, False)
    assert period_flags("2025-04-01", "1900/01/01", "2025/04/01") == (False, True)
    r = mrow()
    r["operation_end_raw"] = "2025/04/01"
    t, _ = add_columns(batch([Decimal(10), Decimal(11)], [date(2025, 4, 1), date(2025, 4, 2)]), [r])
    assert t["capacity_gap_reference_kw"].null_count == 2
    assert t["generation_avg_kw"].null_count == 0
    assert t["qa_period_contradiction"].to_pylist() == [False, True]


def test_exploratory_plant_does_not_permit_different_unit_scope():
    a = dict(area="東京", plant_name="広野火力発電所６号機", unit_name="広野６号")
    h = {"エリア": "東京", "発電所名": "広野火力発電所１号機", "ユニット名": "広野１号"}
    assert not scope_equal(a, h)
    h["ユニット名"] = "広野６号"
    h["発電所名"] = "広野火力発電所"
    assert scope_equal(a, h)
    a["unit_name"] = "姉崎新１号"
    h["ユニット名"] = "姉崎１号"
    assert not scope_equal(a, h)


def test_hold_and_join_validation_leave_generation_rows():
    r = mrow()
    r["research_use_status"] = "held"
    t, _ = add_columns(batch([Decimal(10)]), [r])
    assert len(t) == 1 and t["generation_avg_kw"][0].as_py() == 20
    assert (
        t["capacity_reference_kw"].null_count == 1
        and t["capacity_gap_reference_kw"].null_count == 1
    )
    with pytest.raises(ValueError, match="source重複"):
        add_columns(batch([Decimal(10)]), [r, r])
    r["asset_key_candidate"] = "b"
    with pytest.raises(ValueError, match="脱落"):
        add_columns(batch([Decimal(10)]), [r])


def test_real_gap_output_contract_and_all_row_groups():
    repo = Path(__file__).resolve().parents[1]
    qa = repo / "reports/reference_capacity_gap_qa_20261016.json"
    if not qa.exists():
        pytest.skip("診断の実行後のみ")
    q = json.loads(qa.read_text())
    out = Path(q["output_path"])
    if not out.exists():
        pytest.skip("研究データ保有環境のみ")
    pf = pq.ParquetFile(out)
    assert pf.metadata.num_rows == q["source_rows"] == q["output_rows"] == 8468544
    assert q["raw_generation_missing_cells"] == 214299
    assert q["notation34_used"] == 34
    assert q["mapping_route_use_counts"] == {"strict": 345, "candidate49": 49, "notation34": 34}
    assert all(r["total_observed_generation_kwh"] == "646505684424" for r in q["national_coverage"])
    count = neg = valid = 0
    for b in pf.iter_batches(
        columns=[
            "generation_kwh_30min",
            "generation_avg_kw",
            "capacity_reference_kw",
            "capacity_gap_reference_kw",
            "qa_negative_reference_gap",
        ]
    ):
        raw, avg, cap, gap, flag = [b.column(i) for i in range(5)]
        expected = pc.cast(
            pc.multiply(
                pc.cast(raw, pa.decimal128(32, 6)), pa.scalar(Decimal(2), type=pa.decimal128(1, 0))
            ),
            D,
        )
        assert avg.equals(expected)
        expected_gap = pc.cast(
            pc.subtract(pc.cast(cap, pa.decimal128(32, 6)), pc.cast(avg, pa.decimal128(32, 6))), D
        )
        assert gap.equals(expected_gap)
        count += len(b)
        valid += len(b) - gap.null_count
        neg += pc.sum(pc.cast(flag, pa.int64())).as_py()
    assert count == 8468544 and valid == q["calculable_cells"] and neg == q["negative_gap_cells"]
    assert not q["stop_data_applied"] and not q["final_baseline_adopted"]


def test_full_series_axis_signature_is_preserved():
    a = dict(area="九州", plant_name="新大分発電所", unit_name="１号系列第１軸")
    h = {"エリア": "九州", "発電所名": "新大分発電所", "ユニット名": "２号系列第１軸"}
    assert not scope_equal(a, h)
    h["ユニット名"] = "１号系列第１軸"
    assert scope_equal(a, h)


def test_all_missing_regional_partial_is_null_and_empty_fixed_set_stays_present():
    import pandas as pd

    from src.reference_capacity_gap import regional_partials

    df = pd.DataFrame(
        dict(
            aid=[0],
            area=["東京"],
            day=[0],
            period=[1],
            cap_valid=[True],
            valid=[False],
            cap_micro=[100000000],
            gen_micro=[0],
            gap_micro=[0],
        )
    )
    _, rows = regional_partials(df, set())
    assert len(rows) == 2
    assert all(r["reference_gap_partial_sum_kw"] is None for r in rows)
    assert rows[0]["known_reference_capacity_kw"] == 100
    assert rows[0]["missing_output_in_scope"] == 1
    assert rows[1]["known_reference_capacity_kw"] is None
