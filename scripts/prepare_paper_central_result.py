"""固定年間表から本文用3区分と同じ時刻の価格/構成差を保存。新規LPなし。"""

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.paper_central_result import CATEGORIES, COMMON, KEY, LABELS, describe, three_categories

ROOT = Path(__file__).resolve().parents[1]
ANNUAL = ROOT / "data/processed/annual_diagnostic/20261027_publication_v1"
OUT = ROOT / "data/processed/paper_central_result/20261027_v1"
REPORT = ROOT / "reports/28_paper_central_result"
ENTITIES = ["hokkaido", "tohoku", "jera", "hokuriku", "kansai", "chugoku", "shikoku", "kyushu"]
NAMES = [
    "北海道電力",
    "東北電力",
    "JERA",
    "北陸電力",
    "関西電力",
    "中国電力",
    "四国電力",
    "九州電力",
]
AREAS = ["北海道", "東北", "東京", "中部", "北陸", "関西", "中国", "四国", "九州"]


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    qa = REPORT / "qa"
    inputs = []
    outputs = []

    def read(path):
        inputs.append(dict(path=str(path.relative_to(ROOT)), sha256=sha(path)))
        return pq.read_table(path)

    def write(table, name):
        path = OUT / (name + ".parquet")
        pq.write_table(table, path, compression="zstd")
        assert pq.read_table(path).equals(table)
        outputs.append(dict(path=str(path.relative_to(ROOT)), sha256=sha(path), rows=len(table)))

    original = read(ANNUAL / "explanatory_no_stop_simultaneous.parquet")
    f = original.to_pandas()
    assert len(f) == 280320
    assert f.supply_trial_scenario.eq("reference_no_deduction").all()
    pairs = three_categories(f)
    assert len(pairs) == 140160 and not pairs.duplicated(KEY).any()
    assert pairs.groupby("entity_code").size().eq(17520).all()
    common = pairs[pairs.comparison_scope.eq(COMMON)]
    assert common.groupby("entity_code").size().eq(16848).all()
    reference = None
    for _, g in common.groupby("entity_code"):
        keys = set(zip(g.delivery_date, g.period))
        if reference is None:
            reference = keys
        assert keys == reference
    summary = []
    for code, name in zip(ENTITIES, NAMES):
        g = pairs[pairs.entity_code.eq(code)]
        counts = g.central_category.value_counts()
        record = dict(
            entity_code=code,
            entity_ja=name,
            annual_times=len(g),
            comparison_times=16848,
            excluded_all_firms_infeasible=int(g.comparison_scope.eq("all_firms_infeasible").sum()),
            excluded_network_held=int(g.comparison_scope.eq("held_network_input").sum()),
        )
        for c in CATEGORIES:
            record[c] = int(counts.get(c, 0))
            record[c + "__pct_common"] = record[c] / 16848 * 100
        assert sum(record[c] for c in CATEGORIES) == 16848
        assert (
            record["excluded_all_firms_infeasible"] == 624 and record["excluded_network_held"] == 48
        )
        summary.append(record)
    j = next(r for r in summary if r["entity_code"] == "jera")
    assert [j[c] for c in CATEGORIES] == [767, 14220, 1861]
    pd.DataFrame(summary).to_csv(qa / "central_three_categories.csv", index=False)
    pairs.groupby(["entity_code", "comparison_scope"]).size().rename("frames").reset_index().to_csv(
        qa / "all_times_scope.csv", index=False
    )
    write(pa.Table.from_pandas(pairs, preserve_index=False), "company_all_times_with_paired_status")
    # 元価格付き倍率表を全地域/全時刻のまま残す。主区分は倍率を使わず付与。
    price_original = read(ANNUAL / "explanatory_no_stop_priced.parquet")
    mask = pa.compute.equal(price_original["network_scenario"], "saved_plan_constraints")
    price_original = price_original.filter(mask)
    assert len(price_original) == 157680
    price = price_original.to_pandas()
    joined = price.merge(pairs, on=KEY, how="left", validate="many_to_one", indicator=True)
    assert joined._merge.eq("both").all() and len(joined) == 157680
    assert not joined.duplicated(["target_area", "delivery_date", "period"]).any()
    result = price_original
    for c in pairs.columns:
        if c not in KEY:
            result = result.append_column(c, pa.array(joined[c]))
    assert result.select(price_original.column_names).equals(price_original)
    primary = read(
        ROOT
        / "data/interim/occto/annual_diagnostic/20261027_inputs_v2"
        / "annual_primary_input_ledger.parquet"
    )
    d = primary.select(
        ["area", "delivery_date", "period", "demand_actual_sample_mw", "fixed_actual_mw"]
    ).to_pandas()
    comp = joined.merge(
        d,
        left_on=["target_area", "delivery_date", "period"],
        right_on=["area", "delivery_date", "period"],
        how="left",
        validate="one_to_one",
        indicator="demand_join",
    )
    assert comp.demand_join.eq("both").all()
    for c in ["demand_actual_sample_mw", "fixed_actual_mw"]:
        result = result.append_column(c, pa.array(comp[c]))
    write(result, "price_and_composition_all_times")
    stats = []
    composition = []
    for area in AREAS:
        allarea = comp[comp.target_area.eq(area)]
        scoped = allarea[allarea.comparison_scope.eq(COMMON)]
        assert len(scoped) == 16848
        for cat, label in zip(CATEGORIES, LABELS):
            sub = scoped[scoped.central_category.eq(cat)]
            for variable in [
                "area_price",
                "demand_actual_sample_mw",
                "fixed_actual_mw",
                "target_net_demand_mw",
            ]:
                stats.append(
                    dict(
                        area=area,
                        central_category=cat,
                        category_ja=label,
                        frames=len(sub),
                        variable=variable,
                        **describe(sub[variable]),
                    )
                )
            # 季節/月/時間帯は先に定めた通常区分。価格で閾値を選ばない。
            date = pd.to_datetime(sub.delivery_date)
            month = date.dt.month
            seasons = month.map(
                {
                    3: "春(3–5月)",
                    4: "春(3–5月)",
                    5: "春(3–5月)",
                    6: "夏(6–8月)",
                    7: "夏(6–8月)",
                    8: "夏(6–8月)",
                    9: "秋(9–11月)",
                    10: "秋(9–11月)",
                    11: "秋(9–11月)",
                    12: "冬(12–2月)",
                    1: "冬(12–2月)",
                    2: "冬(12–2月)",
                }
            )
            bands = ((sub.period - 1) // 12).map(
                {0: "00–06時", 1: "06–12時", 2: "12–18時", 3: "18–24時"}
            )
            for dimension, values, levels in [
                ("season", seasons, ["春(3–5月)", "夏(6–8月)", "秋(9–11月)", "冬(12–2月)"]),
                ("time_band", bands, ["00–06時", "06–12時", "12–18時", "18–24時"]),
                (
                    "month",
                    date.dt.strftime("%Y-%m"),
                    [f"2025-{m:02}" for m in range(4, 13)] + ["2026-01", "2026-02", "2026-03"],
                ),
            ]:
                counts = values.value_counts()
                for level in levels:
                    n = int(counts.get(level, 0))
                    composition.append(
                        dict(
                            area=area,
                            central_category=cat,
                            dimension=dimension,
                            level=level,
                            frames=n,
                            category_frames=len(sub),
                            pct=n / len(sub) * 100 if len(sub) else None,
                        )
                    )
    pd.DataFrame(stats).to_csv(qa / "price_demand_fixed_output_by_category.csv", index=False)
    pd.DataFrame(composition).to_csv(qa / "composition_by_season_time_month.csv", index=False)
    status = (
        comp.groupby(["target_area", "comparison_scope", "status"])
        .size()
        .rename("frames")
        .reset_index()
    )
    status.to_csv(qa / "multiplier_all_status_with_scope.csv", index=False)
    missing = []
    for area, g in comp.groupby("target_area"):
        cc = g[g.comparison_scope.eq(COMMON)]
        missing.append(
            dict(
                area=area,
                annual_times=len(g),
                annual_multiplier_valid=int(g.status.eq("valid").sum()),
                annual_uncomputed=int(g.status.ne("valid").sum()),
                common_times=len(cc),
                common_multiplier_valid=int(cc.status.eq("valid").sum()),
                common_uncomputed=int(cc.status.ne("valid").sum()),
            )
        )
    pd.DataFrame(missing).to_csv(qa / "multiplier_missing_in_same_view.csv", index=False)
    # 月別3区分は全体を代表する月の恣意的選択を避ける補助表。
    common = common.copy()
    common["month"] = pd.to_datetime(common.delivery_date).dt.strftime("%Y-%m")
    common.groupby(["entity_code", "month", "central_category"]).size().rename(
        "frames"
    ).reset_index().to_csv(qa / "central_three_categories_monthly.csv", index=False)
    # 同じ元statusから戻す検査：分類と保存元判定が全件同値。
    expected_saved = common.central_category.eq(CATEGORIES[0])
    expected_free = common.central_category.ne(CATEGORIES[2])
    assert expected_saved.equals(common.saved__removed_simultaneous_status.eq("feasible"))
    assert expected_free.equals(common.free__removed_simultaneous_status.eq("feasible"))
    pd.DataFrame([dict(category=c, label_ja=label) for c, label in zip(CATEGORIES, LABELS)]).to_csv(
        qa / "category_dictionary.csv", index=False
    )
    audit = dict(
        annual_company_frames=len(pairs),
        common_company_frames=len(common),
        company_count=8,
        annual_times=17520,
        common_times=16848,
        excluded_all_firms_infeasible_times=624,
        excluded_network_input_times=48,
        common_time_set_equal_for_all_companies=True,
        jera=[j[c] for c in CATEGORIES],
        all_row_sums=16848,
        original_status_equivalence=True,
        price_all_area_times=len(comp),
        price_common_area_times=int(comp.comparison_scope.eq(COMMON).sum()),
        join_rate=1.0,
        unmatched=0,
        row_multiplication=0,
        source_columns_values_types_preserved=True,
        new_lp_calls=0,
        new_annual_data_acquisitions=0,
        final_baseline_adopted=False,
    )
    (qa / "aggregation_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    )
    manifest = dict(
        version="20261027_paper_central_result_v1",
        issue=48,
        dependency_issue=46,
        inputs=inputs,
        outputs=outputs,
        qa=audit,
    )
    (ROOT / "metadata/paper_central_result_manifest_20261027.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(audit, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
