"""研究判断前の比較表と根拠棚卸し。固定版を読み、新規ディレクトリへだけ保存。"""

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from scripts.audit_raw import audit
from src.comparison_hourly import HOUR_KEY, PRICES, hourly_comparison
from src.comparison_interconnector import annotate_comparison
from src.demand_supply import frozen_inputs
from src.market_panel import key_qa
from src.operator_evidence import (
    area_reference_summary,
    business_context_summary,
    inventory_operators,
    load_master,
)
from src.time_normalization import sha256

NATIVE = Path("data/processed/occto/demand_supply_direction/20261016_native_v1")
REPORTS = Path("reports/11_decision_preparation")
NETWORK_FILES = [
    "system_capacity_dayahead2_planned_with_directions_FY2025.parquet",
    "reserve_interconnector_nextday2_with_directions_FY2025.parquet",
    "reserve_interconnector_latest_with_directions_FY2025.parquet",
]
DECIMAL = pa.decimal128(38, 6)


def json_save(path, content):
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2, default=str) + "\n")


def save_table(table, path):
    pq.write_table(table, path, compression="zstd")
    if not pq.read_table(path).equals(table, check_metadata=True):
        raise ValueError(f"Parquet書戻し不一致: {path}")
    return dict(
        path=str(path),
        rows=table.num_rows,
        columns=table.num_columns,
        sha256=sha256(path),
        size=path.stat().st_size,
    )


def frame_table(frame, overrides=None):
    overrides = overrides or {}
    columns, names = [], []
    for name in frame:
        values = frame[name]
        kind = overrides.get(name)
        if kind is None and any(isinstance(v, Decimal) for v in values if pd.notna(v)):
            kind = DECIMAL
        columns.append(pa.array(values, type=kind, from_pandas=True))
        names.append(name)
    return pa.Table.from_arrays(columns, names=names)


def preserve_hourly_source(hourly, source, half):
    present = hourly[f"halfhour_{half}__period"].notna()
    restored = pd.DataFrame(index=hourly.index[present])
    for col in source.columns:
        restored[col] = hourly.loc[present, col if col in HOUR_KEY else f"halfhour_{half}__{col}"]
    wanted = source[source.period.mod(2).eq(half % 2)]
    key = ["area", "delivery_date", "period"]
    pd.testing.assert_frame_equal(
        restored.sort_values(key).reset_index(drop=True),
        wanted.sort_values(key).reset_index(drop=True),
        check_dtype=False,
    )


def run(root, repo, output):
    if output.exists():
        raise FileExistsError("既存出力は上書きしない")
    # 生成前に原本とすべての既存固定版を検査する。
    frozen = frozen_inputs(root, repo)
    raw = audit(root)
    refs = json.loads(
        (repo / "metadata/research/asset_reference_manifest_20261016.json").read_text()
    )
    for row in refs:
        p = root / row["local_path"]
        if sha256(p) != row["sha256"] or p.stat().st_size != row["file_size"]:
            raise ValueError("参考原本SHA不一致")
    native_manifest = json.loads(
        (repo / "metadata/demand_supply_direction_manifest_20261016.json").read_text()
    )
    inputs = list(frozen)
    for row in native_manifest["outputs"]:
        p = root / row["path"]
        if sha256(p) != row["sha256"]:
            raise ValueError("直前ETL固定出力SHA不一致")
        inputs.append(dict(path=str(p), sha256=row["sha256"]))
    evidence_paths = [
        "reports/reference_gap_analysis_mapping_20261016.csv",
        "reports/generation_hjks_677_mapping_candidates.csv",
        "metadata/research/regulatory_targets.csv",
        "metadata/research/operator_mapping_candidates.csv",
        "metadata/research/interconnector_direction_scope_20261016.csv",
    ]
    inputs += [dict(path=str(repo / p), sha256=sha256(repo / p)) for p in evidence_paths]
    output.mkdir(parents=True)
    report = repo / REPORTS / "qa"
    report.mkdir(parents=True, exist_ok=True)
    qa, outputs = {}, []
    market_table = pq.read_table(root / NATIVE / "market_base_FY2025_with_reserve.parquet")
    market = market_table.to_pandas()
    demand_table = pq.read_table(root / NATIVE / "occto_demand_hourly_FY2025.parquet")
    demand = demand_table.to_pandas()
    hourly, stats, unused = hourly_comparison(market, demand)
    key_qa(hourly, HOUR_KEY, expected=9 * 8760)
    for half in (1, 2):
        preserve_hourly_source(hourly, market, half)
    valid = hourly.demand_source_key_matched
    for col in demand.columns:
        restored = hourly.loc[valid, col if col in HOUR_KEY else "demand_source__" + col]
        ordered = (
            demand.set_index(HOUR_KEY).loc[
                pd.MultiIndex.from_frame(hourly.loc[valid, HOUR_KEY]), col
            ]
            if col not in HOUR_KEY
            else None
        )
        if ordered is not None and restored.tolist() != ordered.tolist():
            raise ValueError("需要の元セル不一致")
    overrides = {}
    for half in (1, 2):
        overrides.update(
            {
                f"halfhour_{half}__{f.name}": f.type
                for f in market_table.schema
                if f.name not in HOUR_KEY
            }
        )
    overrides.update(
        {"demand_source__" + f.name: f.type for f in demand_table.schema if f.name not in HOUR_KEY}
    )
    hourly_path = output / "market_hourly_price_demand_FY2025.parquet"
    outputs.append(save_table(frame_table(hourly, overrides), hourly_path))
    unused.to_csv(report / "hourly_unused_demand_keys.csv", index=False)
    price_stats = {}
    for name in PRICES:
        delta = hourly[f"{name}__absolute_halfhour_difference"]
        price_stats[name] = dict(
            calculable=int(hourly[f"{name}__calculation_available"].sum()),
            nonzero_halfhour_difference=int(delta.gt(0).sum()),
            max_halfhour_difference=str(max(delta.dropna())),
            mean_absolute_halfhour_difference=str(sum(delta.dropna()) / len(delta.dropna())),
        )
    stats.update(
        price_statistics=price_stats,
        original_market_cells_equal=True,
        original_demand_cells_equal=True,
        original_types_preserved=True,
        demand_unit_display_conflict_rows=int(
            hourly["demand_source__qa_hourly_unit_display_conflict"].sum()
        ),
    )
    qa["hourly"] = stats
    # 数値例は抜粋として保存し、研究サンプルの選択・除外には使わない。
    sample_cols = HOUR_KEY + [
        "halfhour_1__area_price",
        "halfhour_2__area_price",
        "area_price__time_mean",
        "area_price__minimum",
        "area_price__maximum",
        "area_price__second_minus_first",
        "demand_source__demand_hourly_csv_mwh",
    ]
    hourly.sort_values("area_price__absolute_halfhour_difference", ascending=False).head(5)[
        sample_cols
    ].to_csv(report / "hourly_price_examples.csv", index=False)
    qa["network"] = {}
    for number, filename in enumerate(NETWORK_FILES):
        original = pq.read_table(root / NATIVE / filename)
        frame = original.to_pandas()
        added, network_qa = annotate_comparison(frame, market, system=number == 0)
        overrides = {c: DECIMAL for c in added if c.endswith(("_price", "_mw"))}
        additions = frame_table(added, overrides)
        result = original
        for field, col in zip(additions.schema, additions.columns, strict=True):
            result = result.append_column(field, col)
        if not result.select(original.column_names).equals(original, check_metadata=True):
            raise ValueError("連系線元セル/型不一致")
        key_qa(frame, ["source_record_id"])
        target = output / filename.replace("_with_directions_", "_direction_comparison_")
        outputs.append(save_table(result, target))
        network_qa["original_cells_types_equal"] = True
        network_qa["nulls_in_added_columns"] = {c: result[c].null_count for c in added}
        qa["network"][filename] = network_qa
        pick = ["delivery_date", "period", "link_name_raw"]
        if number == 0:
            pick += ["direction_raw", "sending_area", "receiving_area", "available_capacity_mw"]
            cols = [
                "available_capacity_direction_display_mw",
                "sending_area_price",
                "receiving_area_price",
                "receiving_minus_sending_price",
                "price_comparison_status",
            ]
        else:
            pick += ["forward__sending_area", "forward__receiving_area"]
            cols = [
                "forward_available_capacity_comparison_mw",
                "forward__sending_area_price",
                "forward__receiving_area_price",
                "forward__receiving_minus_sending_price",
                "forward__price_comparison_status",
            ]
        example = pd.concat([frame[pick].head(10), added[cols].head(10)], axis=1)
        example.to_csv(report / f"network_{number}_examples.csv", index=False)
    prior = json.loads((repo / "reports/generation_hjks_FY2025_qa.json").read_text())
    master, snapshot = load_master(root, prior)

    def read(p):
        return pd.read_csv(repo / p, dtype=str, keep_default_na=False)

    assets = read("reports/generation_hjks_677_mapping_candidates.csv")
    mapping = read("reports/reference_gap_analysis_mapping_20261016.csv")
    targets = read("metadata/research/regulatory_targets.csv")
    ledger, edges, exclusive, candidates, name_compare, op_qa = inventory_operators(
        assets, mapping, master, snapshot, targets
    )
    if len(ledger) != 491:
        raise ValueError("全491unitを保持できていない")
    for title, data in [
        ("observed_unit_business_evidence", ledger),
        ("hjks_business_candidate_rows", edges),
        ("business_regulatory_name_comparison", name_compare),
    ]:
        outputs.append(save_table(frame_table(data), output / f"{title}_FY2025.parquet"))
    for title, data in [
        ("unit_business_inventory", ledger),
        ("business_area_exclusive", exclusive),
        ("business_area_candidate_ranges", candidates),
        ("business_regulatory_name_comparison", name_compare),
    ]:
        # 読み物用QAでは毎日の配列を再掲せず、全元列はParquetへ保持。
        summary = (
            data.drop(columns=["observed_dates"], errors="ignore")
            if title == "unit_business_inventory"
            else data
        )
        summary.to_csv(report / f"{title}.csv", index=False)
    if sum(exclusive.observed_unit_labels) != 491 or sum(exclusive.observed_generation_kwh) != sum(
        ledger.generation_sum_kwh
    ):
        raise ValueError("法人主集計の重複加算/脱落")
    area_reference_summary(ledger).to_csv(report / "operator_area_coverage.csv", index=False)
    context = yaml.safe_load(
        (repo / "metadata/research/decision_preparation_official_evidence_20261016.yml").read_text()
    )
    business_context_summary(ledger, context["corporate_contexts"]).to_csv(
        report / "major_business_context.csv", index=False
    )
    qa["operators"] = op_qa
    # 直前・固定版・原本・対応表を処理後も照合。
    for row in inputs:
        if sha256(Path(row["path"])) != row["sha256"]:
            raise ValueError("処理中に既存入力が変更された")
    post = audit(root)
    qa["immutability"] = dict(
        registered_raw=raw["registered_file_count"],
        reference_raw=len(refs),
        fixed_inputs=len(frozen),
        native_outputs=len(native_manifest["outputs"]),
        checked_input_paths=len(inputs),
        registered_raw_after=post["registered_file_count"],
        all_passed=True,
    )
    qa["scope"] = dict(
        comparison_only=True,
        baseline_chosen=False,
        generation_or_reference_gap_hourly_aggregation=False,
        import_capacity_aggregation=False,
        supplier_classification=False,
    )
    # 元の出力先を変更せず、入力manifestも共有repo/コードrepo基準で出典を保持。
    manifest_inputs = []
    for row in inputs:
        p = Path(row["path"])
        base = repo if p.is_relative_to(repo) and not p.is_relative_to(root / "data") else root
        manifest_inputs.append(
            dict(
                path=str(p.relative_to(base)),
                sha256=row["sha256"],
                location="data_root" if p.is_relative_to(root / "data") else "code_repo",
            )
        )
    qa["output_null_counts"] = {}
    for entry in outputs:
        stored = pq.read_table(entry["path"])
        qa["output_null_counts"][Path(entry["path"]).name] = {
            name: stored[name].null_count for name in stored.column_names
        }
    # 出力manifestのパスは相対パスで他環境にも再利用可能にする。
    for row in outputs:
        row["path"] = str(Path(row["path"]).relative_to(root))
    manifest = dict(
        version=output.name,
        inputs=manifest_inputs,
        outputs=outputs,
        document_evidence=["metadata/research/decision_preparation_official_evidence_20261016.yml"],
        source_columns_preserved=True,
        final_research_baseline=False,
    )
    json_save(repo / "metadata/decision_preparation_manifest_20261016.json", manifest)
    json_save(report / "qa.json", qa)
    return qa
