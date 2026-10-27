"""既存年間9供給条件から、原列保持の2設備追加・17条件を構築する。"""

import gc
import hashlib
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.trial_adjustable_removal import ENTITIES  # noqa: E402
from src.adjustable_removal import RECORD_HELD  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/interim/occto/annual_diagnostic/20261027_supply_v2"
OLD = ROOT / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1/unit_scenarios"
IDS = [
    "182a25b5db172b0c99d48004bd40c1b57bf12e5e0cb918181cd8d5fbb5a0bbbe",
    "1f70aac24549c68ac6712af93662e5e0d2df247faac3cb3e09f0b8c1f1b12e88",
]


def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for sub in ["unit_scenarios", "regional", "checkpoints"]:
        (DEST / sub).mkdir(exist_ok=True)
    assets = pq.read_table(
        ROOT
        / "data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3"
        / "asset_attribution_proxy.parquet"
    )
    allowed = pc.or_(
        assets["eligible_adjustable_candidate"],
        pc.is_in(assets["asset_key_candidate"], value_set=pa.array(IDS)),
    )
    ids = pc.filter(assets["asset_key_candidate"], allowed)
    codes = assets["removal_entity_code"]
    for path in sorted(OLD.glob("*.parquet")):
        orig = pq.read_table(path)
        assert len(orig) == 8468544
        eligible = pc.is_in(orig["asset_key_candidate"], value_set=ids)
        selected = pc.filter(orig, eligible)
        entity = pc.take(
            codes,
            pc.index_in(selected["asset_key_candidate"], value_set=assets["asset_key_candidate"]),
        )
        ref = selected["capacity_reference_kw"]
        for restore in [False] if path.stem == "reference_no_deduction" else [False, True]:
            name = path.stem + ("__record_held_reference" if restore else "")
            ck = DEST / "checkpoints" / (name + ".json")
            if ck.exists():
                record = json.loads(ck.read_text())
                assert sha(DEST / "regional" / (name + ".parquet")) == record["regional_sha256"]
                assert sha(DEST / "unit_scenarios" / (name + ".parquet")) == record["unit_sha256"]
                assert sha(path) == record["source_sha256"]
                continue
            print("supply", name, flush=True)
            full_restore = (
                pc.and_(
                    pc.is_in(orig["scenario_status"], value_set=pa.array(sorted(RECORD_HELD))),
                    pc.fill_null(pc.greater(orig["capacity_reference_kw"], 0), False),
                )
                if restore
                else pa.chunked_array([pa.array([False] * len(orig))])
            )
            full_restore = pc.and_(eligible, full_restore)
            upper = pc.if_else(
                eligible,
                pc.if_else(
                    full_restore, orig["capacity_reference_kw"], orig["adjusted_reference_upper_kw"]
                ),
                None,
            )
            defined = pc.fill_null(pc.greater_equal(upper, 0), False)
            unit = (
                orig.append_column("annual_eligible_adjustable_candidate", eligible)
                .append_column("annual_adjustable_upper_kw", upper)
                .append_column("annual_record_held_reference_restored", full_restore)
                .append_column("annual_upper_defined", defined)
            )
            upath = DEST / "unit_scenarios" / (name + ".parquet")
            pq.write_table(unit, upath, compression="zstd")
            reread = pq.read_table(upath)
            assert reread.select(orig.column_names).equals(orig)
            u = pc.filter(upper, eligible)
            ok = pc.filter(defined, eligible)
            rest = pc.filter(full_restore, eligible)
            fields = {k: selected[k] for k in ["area", "delivery_date", "period"]}
            fields.update(
                upper_kw=pc.if_else(ok, u, None),
                observed_candidate_count=pa.array([1] * len(selected), type=pa.int8()),
                defined_candidate_count=pc.cast(ok, pa.int8()),
                held_candidate_count=pc.cast(pc.invert(ok), pa.int8()),
                restored_candidate_count=pc.cast(rest, pa.int8()),
                restored_reference_kw=pc.if_else(rest, ref, 0),
                held_reference_kw=pc.if_else(pc.invert(ok), ref, 0),
                unknown_capacity_count=pc.cast(pc.is_null(ref), pa.int8()),
            )
            for code in ENTITIES:
                fields["remove_" + code + "_kw"] = pc.if_else(
                    pc.and_(ok, pc.equal(entity, code)), u, 0
                )
            table = pa.table(fields)
            reg = table.group_by(["area", "delivery_date", "period"]).aggregate(
                [(k, "sum") for k in fields if k not in ["area", "delivery_date", "period"]]
            )
            reg = reg.sort_by(
                [("area", "ascending"), ("delivery_date", "ascending"), ("period", "ascending")]
            )
            renamed = []
            for col in reg.column_names:
                target = col.removesuffix("_sum")
                if target.endswith("_kw"):
                    target = target[:-3] + "_mw"
                    values = pc.cast(
                        pc.divide(
                            pc.cast(reg[col], pa.decimal128(28, 6)),
                            pa.scalar(1000, pa.decimal128(4, 0)),
                        ),
                        pa.decimal128(38, 9),
                    )
                else:
                    values = reg[col]
                if target == "upper_mw":
                    target = "upper_limited_mw"
                renamed.append((target, values))
            reg = pa.table(dict(renamed)).append_column(
                "supply_trial_scenario", pa.array([name] * len(reg))
            )
            assert len(reg) == 157680
            rpath = DEST / "regional" / (name + ".parquet")
            pq.write_table(reg, rpath, compression="zstd")
            record = dict(
                scenario=name,
                source_path=str(path.relative_to(ROOT)),
                source_sha256=sha(path),
                unit_rows=len(unit),
                unit_original_columns_values_types_equal=True,
                unit_sha256=sha(upath),
                regional_rows=len(reg),
                regional_sha256=sha(rpath),
                eligible_asset_labels=len(ids),
                restore_record_held=restore,
                unknown_supply_is_zero=False,
            )
            ck.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            del unit, reread, reg, table, fields, upper
            gc.collect()
        del orig, selected
        gc.collect()
    records = [json.loads(p.read_text()) for p in sorted((DEST / "checkpoints").glob("*.json"))]
    assert len(records) == 17
    (ROOT / "reports/27_fy2025_annual/qa/annual_supply_audit.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    )
    print("annual supply complete", len(records), flush=True)


if __name__ == "__main__":
    main()
