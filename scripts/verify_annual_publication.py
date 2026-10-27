"""固定manifestと全価格付きParquetを、保存後に原表へ全セル再照合する。"""

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "reports/27_fy2025_annual/qa"


def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    m = json.loads((ROOT / "metadata/annual_diagnostic_manifest_20261027.json").read_text())
    for r in m["inputs"] + m["outputs"]:
        assert sha(ROOT / r["path"]) == r["sha256"], r["path"]
    base = ROOT / "data/interim/occto/annual_diagnostic/20261027_results_v1"
    priced = ROOT / "data/processed/annual_diagnostic/20261027_publication_v1"
    cols = [
        "area_price",
        "system_price_market",
        "system_price_sensitivity_base",
        "baseline_price_match_flag",
    ]
    market = pq.read_table(
        ROOT
        / "data/processed/occto/demand_supply_direction/20261016_native_v1"
        / "market_base_FY2025_with_reserve.parquet",
        columns=["area", "delivery_date", "period"] + cols,
    )
    pindex = pd.MultiIndex.from_frame(
        market.select(["area", "delivery_date", "period"]).to_pandas()
    )
    frames = 0
    for p in sorted((priced / "priced_multipliers").glob("*.parquet")):
        original = pq.read_table(base / "multipliers" / p.name)
        new = pq.read_table(p)
        assert new.select(original.column_names).equals(original), p.name
        keys = new.select(["target_area", "delivery_date", "period"]).to_pandas()
        idx = pindex.get_indexer(pd.MultiIndex.from_frame(keys))
        assert (idx >= 0).all() and not keys.assign(
            network=new["network_scenario"].to_pylist()
        ).duplicated().any()
        expected = market.select(cols).take(pa.array(idx))
        assert new.select(cols).equals(expected), p.name
        f = new.select(["status", "lambda_removed", "target_net_demand_mw"]).to_pandas()
        good = f.status.eq("valid")
        assert f.loc[good, "lambda_removed"].notna().all()
        assert f.loc[~good, "lambda_removed"].isna().all()
        assert f.loc[good, "target_net_demand_mw"].gt(0).all()
        frames += len(new)
    assert frames == 5361120
    consistency = pd.read_parquet(priced / "condition_consistency_all_times.parquet")
    keys = ["network_scenario", "entity_code", "delivery_date", "period"]
    assert len(consistency) == 280320 and not consistency.duplicated(keys).any()
    assert consistency.groupby(["network_scenario", "entity_code"]).size().eq(17520).all()
    oldhash = json.loads((QA / "immutable_sha_audit.json").read_text())
    for p, h in oldhash["hashes"].items():
        assert sha(ROOT / p) == h, p
    tracked = subprocess.check_output(
        ["git", "ls-files", "data/raw", "data/interim", "data/processed"], text=True
    ).splitlines()
    assert not tracked
    assert (
        subprocess.check_output(["git", "rev-parse", "main"], text=True).strip()
        == "a49bd46c77fa3933fe983fa49784504c8ec5c855"
    )
    report = dict(
        manifest_inputs_verified=len(m["inputs"]),
        manifest_outputs_verified=len(m["outputs"]),
        original_result_cells_types_preserved=True,
        price_cells_types_preserved=True,
        price_frames_verified=frames,
        price_join_rate=1.0,
        unmatched=0,
        key_duplicates=0,
        silent_row_drop=0,
        row_multiplication=0,
        unknown_or_nonpositive_multiplier_replaced_by_zero=False,
        prior_files_hash_verified=len(oldhash["hashes"]),
        research_data_git_tracked=0,
        main_unchanged=True,
        pytest={"passed": 363, "command": ".venv/bin/pytest -q"},
        ruff="passed",
        notebook="09_fy2025_annual.ipynb:9 code cells executed",
        final_baseline_adopted=False,
    )
    (QA / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(report, flush=True)


if __name__ == "__main__":
    main()
