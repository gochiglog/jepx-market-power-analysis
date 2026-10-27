"""採用する年間診断の入力・出力・列定義・再開SHAを固定する。"""

import csv
import hashlib
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_annual_supply import IDS
from scripts.run_annual_diagnostic import OUT, ROOT

MANIFEST = ROOT / "metadata/annual_diagnostic_manifest_20261027.json"
QA = ROOT / "reports/27_fy2025_annual/qa"
SUPPLY = ROOT / "data/interim/occto/annual_diagnostic/20261027_supply_v2"
PUBLICATION = ROOT / "data/processed/annual_diagnostic/20261027_publication_v1"


def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


DEFINITIONS = {
    "demand_actual_sample_mw": ("公表実績需要D（同一一次CSVの30分平均）", "MW"),
    "pv_actual_sample_mw": ("太陽光の公表実績（制御量を加算しない）", "MW"),
    "wind_actual_sample_mw": ("風力の公表実績（制御量を加算しない）", "MW"),
    "nuclear_regional_sample_mw": ("原子力の地域公表実績", "MW"),
    "fixed_actual_mw": ("分析上固定するPV＋風力＋原子力の実績R", "MW"),
    "net_demand_B_mw": ("元の純需要N=D−R。非正を切り上げない", "MW"),
    "pumped_net_injection_mw": ("公表揚水の符号付き純注入。総充電でない", "MW"),
    "battery_net_injection_mw": ("公表蓄電池の符号付き純注入。空欄は未知", "MW"),
    "net_import_from_actual_csv_mw": ("CSV内連系線の純受電。九州だけ会計列の符号反転", "MW"),
    "annual_eligible_adjustable_candidate": ("既存9地域372＋公式非重複確認2の調整候補", "boolean"),
    "annual_adjustable_upper_kw": ("同じ停止条件で定義する調整参照上限。負値も原列を保持", "kW"),
    "annual_record_held_reference_restored": (
        "記録由来保留へ追加控除なしの比較を適用した",
        "boolean",
    ),
    "annual_upper_defined": ("調整参照上限が既知・非負。未知能力0を意味しない", "boolean"),
    "upper_limited_mw": ("計算可能な限定調整集合の地域参照上限部分和", "MW"),
    "observed_candidate_count": ("元観測行のある調整候補ラベル数", "label"),
    "defined_candidate_count": ("上限を定義できる調整候補数", "label"),
    "held_candidate_count": ("観測された調整候補のうち上限保留数", "label"),
    "unknown_capacity_count": ("観測調整候補内で参照容量自体が不明な数", "label"),
    "held_reference_mw": ("保留調整候補について既知参照値のみの部分和", "MW"),
    "restored_reference_mw": ("記録保留に追加控除なしを適用して戻す参照上限", "MW"),
    "lambda_all_firms": ("他地域N固定・対象Nだけ拡大する全法人最大倍率", "ratio"),
    "lambda_removed": ("固定Rを残し対象名参照調整供給を除いた最大倍率", "ratio"),
    "status": ("対象N非正→全法人不成立→入力保留→倍率問題判定の主区分", "category"),
    "all_firms_status": ("元N固定の9地域同時成立：全法人の独立判定", "category"),
    "all_firms_balance_status": ("全法人の元N固定同時成立。倍率とは別検査", "category"),
    "removed_simultaneous_status": ("対象名参照調整供給を除いた元N固定9地域同時成立", "category"),
    "target_net_demand_mw": ("評価地域の元の純需要。実績発電を追加控除しない", "MW"),
    "national_known_upper_mw": ("9地域の既知調整上限部分和。全供給力ではない", "MW"),
    "national_remaining_known_upper_mw": ("企業除去後の9地域既知調整上限部分和", "MW"),
    "national_net_demand_mw": ("9地域の元の純需要合計", "MW"),
    "removed_adjustable_mw_all_nine_areas": ("同じ供給条件で除いた対象法人名参照分の9地域和", "MW"),
    "removed_fixed_output_mw": ("固定Rの除去量。今回全枠0（固定Rを残す）", "MW"),
    "area_price": ("当該地域のJEPX spotエリア価格", "JPY/kWh"),
    "system_price_market": ("JEPX spotの実績system price。全国共通反復", "JPY/kWh"),
    "system_price_sensitivity_base": ("価格感応度の別計算基準。spotへ置換しない", "JPY/kWh"),
    "baseline_price_match_flag": (
        "spot実績と感応度別基準の一致。年度不一致2,271時刻を保持",
        "boolean",
    ),
    "network_input_status": ("公表制約が揃うか。3/13の48コマは保留", "category"),
    "coefficient_version": ("旧11制約／新フェンス10制約の期間契約", "category"),
    "consistency": ("17条件内の同時成立の排他的な一致/条件依存/比較対象外", "category"),
    "supply_trial_scenario": ("停止控除なし＋既存停止8＋追加控除なし比較8の17条件", "category"),
    "network_scenario": ("保存留保枠B／同じ接続で包絡のみ解除の代替条件", "category"),
    "target_area": ("倍率評価地域。JERA東京/中部は別地域", "category"),
    "entity_code": ("モデル内全地域から同時に除く名参照法人コード。JERAは一法人", "category"),
    "delivery_date": ("受渡日、Asia/Tokyo", "date"),
    "period": ("受渡日の30分コマ1〜48。1は00:00–00:30", "period"),
}


def dictionary(paths):
    existing = {}
    with (ROOT / "metadata/variable_dictionary.csv").open() as f:
        for r in csv.DictReader(f):
            existing.setdefault(r["variable_name"], r)
    rows = []
    for group, path in paths.items():
        for field in pq.read_schema(path):
            name = field.name
            inherited = existing.get(name, {})
            definition, unit = DEFINITIONS.get(
                name,
                (
                    inherited.get(
                        "definition", "元列/出典識別子または診断補助列。元表の定義と文脈を保持"
                    ),
                    inherited.get("unit", "not_applicable"),
                ),
            )
            if name.startswith("remove_") and name.endswith("_mw"):
                definition, unit = "当該法人の調整参照上限部分和（同じ停止条件で除去）", "MW"
            rows.append(
                dict(
                    table_group=group,
                    column_name=name,
                    type=str(field.type),
                    definition_ja=definition,
                    unit=unit,
                    example_file=str(path.relative_to(ROOT)),
                    limitation="限定集合/固定R/保存留保枠/名参照。確認済み利用可能性ではない",
                )
            )
    with (QA / "output_column_dictionary.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    # 共通辞書へ今回の派生定義のみ追加。元データの列は改名しない。
    path = ROOT / "metadata/variable_dictionary.csv"
    with path.open() as f:
        allrows = list(csv.DictReader(f))
        fields = list(allrows[0])
    already = {(r["source_id"], r["variable_name"]) for r in allrows}
    appended = []
    for name, (definition, unit) in DEFINITIONS.items():
        if ("derived", "annual__" + name) in already:
            continue
        kind = (
            "quantity"
            if unit in {"MW", "kW", "ratio", "JPY/kWh"}
            else "boolean"
            if unit == "boolean"
            else "date"
            if unit == "date"
            else "integer"
            if unit in {"period", "label"}
            else "string"
        )
        safe_unit = unit if kind == "quantity" else "not_applicable"
        appended.append(
            dict(
                source_id="derived",
                variable_name="annual__" + name,
                definition=definition,
                kind=kind,
                unit=safe_unit,
                source_unit=safe_unit,
                source_column=name,
                status="specified",
                notes="Issue46。FY2025/17条件。診断専用、最終baselineではない",
            )
        )
    # 既存辞書のバイト/引用/改行を保ち、追加行だけ末尾へ追記する。
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writerows(appended)


def main():
    if MANIFEST.exists():
        raise ValueError("既存固定manifestを置換しない")
    original = pq.read_table(
        ROOT
        / "data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3"
        / "asset_attribution_proxy.parquet"
    )
    eligible = pc.or_(
        original["eligible_adjustable_candidate"],
        pc.is_in(original["asset_key_candidate"], value_set=pa.array(IDS)),
    )
    extra = pc.is_in(original["asset_key_candidate"], value_set=pa.array(IDS))
    register = original.append_column(
        "annual_eligible_adjustable_candidate", eligible
    ).append_column("annual_external_nonoverlap_evidence_used", extra)
    path = SUPPLY / "annual_asset_role_register.parquet"
    assert not path.exists()
    pq.write_table(register, path, compression="zstd")
    assert pq.read_table(path).select(original.column_names).equals(original)
    dictionary(
        dict(
            primary=ROOT
            / "data/interim/occto/annual_diagnostic/20261027_inputs_v2"
            / "annual_primary_input_ledger.parquet",
            unit=SUPPLY / "unit_scenarios/reference_no_deduction.parquet",
            regional=SUPPLY / "regional/reference_no_deduction.parquet",
            multipliers=PUBLICATION / "priced_multipliers/202504__reference_no_deduction.parquet",
            simultaneous=OUT / "simultaneous/202504__reference_no_deduction.parquet",
            all_firms=OUT / "all_firms/202504__reference_no_deduction.parquet",
            network=ROOT
            / "data/interim/occto/annual_diagnostic/20261027_network_v1"
            / "annual_network_constraints.parquet",
            consistency=PUBLICATION / "condition_consistency_all_times.parquet",
            asset_role_register=path,
        )
    )
    for ck in sorted((OUT / "checkpoints").glob("*.json")):
        r = json.loads(ck.read_text())
        files = r["outputs"] + [
            str((OUT / "samples" / (r["month"] + ".parquet")).relative_to(ROOT))
        ]
        r["output_sha256"] = {x: sha(ROOT / x) for x in files}
        ck.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n")
    outputs = []
    for folder in [
        ROOT / "data/interim/occto/annual_diagnostic" / x
        for x in [
            "20261027_inputs_v2",
            "20261027_supply_v2",
            "20261027_network_v1",
            "20261027_results_v1",
        ]
    ] + [PUBLICATION]:
        for p in sorted(folder.rglob("*")):
            if not p.is_file():
                continue
            r = dict(path=str(p.relative_to(ROOT)), sha256=sha(p), bytes=p.stat().st_size)
            if p.suffix == ".parquet":
                m = pq.read_metadata(p)
                r.update(rows=m.num_rows, columns=m.num_columns)
            outputs.append(r)
    inputs = []
    contracts = json.loads((QA / "annual_input_file_contracts.json").read_text())
    seen = set()
    for r in contracts:
        if r["source_file"] in seen:
            continue
        seen.add(r["source_file"])
        inputs.append(dict(path=r["source_file"], sha256=r["source_sha256"]))
    for r in json.loads((QA / "annual_supply_audit.json").read_text()):
        if r["source_path"] not in seen:
            seen.add(r["source_path"])
            inputs.append(dict(path=r["source_path"], sha256=r["source_sha256"]))
    inputs.append(
        dict(
            path="data/interim/occto/saved_plan_supply/20261024_diagnostic_v1/network_candidate_bounds.parquet",
            sha256=sha(
                ROOT
                / "data/interim/occto/saved_plan_supply/20261024_diagnostic_v1"
                / "network_candidate_bounds.parquet"
            ),
        )
    )
    inputs.append(
        dict(
            path="data/processed/occto/demand_supply_direction/20261016_native_v1/market_base_FY2025_with_reserve.parquet",
            sha256=sha(
                ROOT
                / "data/processed/occto/demand_supply_direction/20261016_native_v1"
                / "market_base_FY2025_with_reserve.parquet"
            ),
        )
    )
    manifest = dict(
        version="20261027_FY2025_diagnostic_v1",
        issue=46,
        branch="issue/feature-46",
        dependency_branch="issue/feature-44",
        timezone="Asia/Tokyo",
        periods_per_day=48,
        total_times=17520,
        network_ready_times=17472,
        network_held_times=48,
        unit_collection="374adjustable_candidates_including_Muroran100_Kushiro112",
        fixed_output="actual_PV_wind_nuclear_retained",
        supply_conditions=17,
        entities=8,
        target_pairs=9,
        inputs=inputs,
        outputs=outputs,
        final_baseline_adopted=False,
        old_fixed_outputs_modified=False,
        intermediate_unadopted=["20261027_inputs_v1", "20261027_supply_v1"],
        interpretation="限定集合・名参照・保存留保枠B・公表需要のみ。原TCRSI/企業不可欠性/実行使の認定ではない",
    )
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("固定manifest", len(inputs), "inputs", len(outputs), "outputs", flush=True)


if __name__ == "__main__":
    main()
