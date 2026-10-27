"""保存制約の上下限・元行参照と、新旧固定版のSHAを独立照合する。"""

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def verify():
    manifest = ROOT / "metadata/net_demand_network_manifest_20261026.json"
    qa = json.loads(manifest.read_text())
    paths = {Path(x["path"]).name: ROOT / x["path"] for x in qa["outputs"]}
    constraints = pq.read_table(paths["legacy_network_constraints_FY2025.parquet"]).to_pandas()
    oldnet = (
        ROOT
        / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1"
        / "network_candidate_bounds.parquet"
    )
    source = pq.read_table(
        oldnet,
        columns=[
            "source_record_id",
            "delivery_date",
            "period",
            "link_name_raw",
            "direction_raw",
            "operating_capacity_mw",
            "margin_mw",
            "wide_area_adjustment_mw",
            "planned_flow_mw",
        ],
    ).to_pandas()
    if source.source_record_id.duplicated().any():
        raise ValueError("元行ID重複")
    source = source.set_index("source_record_id")
    pairs = constraints.source_ids_json.map(json.loads)
    references = [x for pair in pairs for x in pair]
    if len(references) != len(set(references)):
        raise ValueError("元制約行の多重使用")
    # 実装のcompile関数を呼ばず、両方向の原行と派生上下限を独立に対応付ける。
    for position, column, direction in [(0, "upper_mw", "順方向"), (1, "lower_mw", "逆方向")]:
        ids = pairs.map(lambda x: x[position]).tolist()
        selected = source.loc[ids].reset_index()
        for key in ["delivery_date", "period"]:
            if selected[key].tolist() != constraints[key].tolist():
                raise ValueError("時刻がずれた")
        if not selected.direction_raw.eq(direction).all():
            raise ValueError("方向がずれた")
        if selected.link_name_raw.tolist() != constraints.constraint_name.tolist():
            raise ValueError("名称がずれた")
        bound = (
            selected.operating_capacity_mw - selected.margin_mw - selected.wide_area_adjustment_mw
        )
        if bound.tolist() != constraints[column].tolist():
            raise ValueError("上下限が原値と異なる")
    all_rows = source.loc[references]
    if len(all_rows) != 365376:
        raise ValueError("対象原行数")
    fixed = json.loads((ROOT / "metadata/saved_plan_supply_manifest_20261024.json").read_text())
    count = 0
    for x in fixed["inputs"] + fixed["outputs"] + fixed["provenance_tables"]:
        if digest(ROOT / x["path"]) != x["sha256"]:
            raise ValueError("Issue40の固定入力/出力が変更")
        count += 1
    for x in qa["inputs"] + qa["outputs"]:
        if digest(ROOT / x["path"]) != x["sha256"]:
            raise ValueError("新版の入力/出力SHA不一致")
    readiness = pq.read_table(paths["network_time_readiness_FY2025.parquet"]).to_pandas()
    assert len(readiness) == 17520 and not readiness.duplicated(["delivery_date", "period"]).any()
    assert not readiness.real_all_firm_lp_run.any()
    references = json.loads(
        (ROOT / "metadata/research/net_demand_reference_manifest_20261026.json").read_text()
    )
    for x in references:
        if "local_path" in x and digest(ROOT / x["local_path"]) != x["sha256"]:
            raise ValueError("参考原本SHA変更")
    result = dict(
        constraint_rows=len(constraints),
        source_row_matches=365376,
        source_row_unmatched=0,
        duplicate_references=0,
        original_bound_all_values_equal=True,
        issue40_fixed_hash_checks=count,
        new_reference_hash_checks=sum("local_path" in x for x in references),
        real_all_firm_lp_runs=0,
        net_demand_nonpositive_count=None,
        source_and_processed_times_and_directions_equal=True,
        raw_planned_flow_nonzero_rows=int(source.planned_flow_mw.ne(0).sum()),
        original_rows_held_for_cutover_scope=len(source) - len(all_rows),
    )
    qa["independent_verification"] = result
    qa["raw_planned_flow_nonzero_rows"] = result["raw_planned_flow_nonzero_rows"]
    qa["definition_evidence"] = [
        dict(path=str(p.relative_to(ROOT)), sha256=digest(p))
        for p in [
            ROOT / "metadata/research/net_demand_network_sources_20261026.yml",
            ROOT / "metadata/research/net_demand_network_contract_20261026.yml",
            ROOT / "metadata/research/net_demand_reference_manifest_20261026.json",
        ]
    ]
    manifest.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
    (ROOT / "reports/17_net_demand_network/qa/audit.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    verify()
