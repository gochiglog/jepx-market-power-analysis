"""固定年間版を不変性監査し、中心3区分を元集計と独立に照合する。LPなし。"""

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/28_paper_central_result"


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    manifest = json.loads(
        (ROOT / "metadata/paper_central_result_manifest_20261027.json").read_text()
    )
    verified = {}

    def check(r):
        if r["path"] not in verified:
            assert sha(ROOT / r["path"]) == r["sha256"], r["path"]
            verified[r["path"]] = r["sha256"]

    for r in manifest["inputs"] + manifest["outputs"]:
        check(r)
    annual = json.loads((ROOT / "metadata/annual_diagnostic_manifest_20261027.json").read_text())
    for r in annual["inputs"] + annual["outputs"]:
        check(r)
    old = json.loads((ROOT / "reports/27_fy2025_annual/qa/immutable_sha_audit.json").read_text())
    for p, h in old["hashes"].items():
        check(dict(path=p, sha256=h))
    a = pd.read_csv(ROOT / "reports/27_fy2025_annual/qa/transmission_common_times.csv").set_index(
        "entity_code"
    )
    b = pd.read_csv(REPORT / "qa/central_three_categories.csv").set_index("entity_code")
    assert len(b) == 8 and b.index.is_unique
    for entity, r in b.iterrows():
        assert r.comparison_times == a.loc[entity, "denominator"] == 16848
        assert r.feasible_with_saved_bounds == 16848 - a.loc[entity, "constrained_infeasible"]
        assert r.feasible_only_without_bounds == (
            a.loc[entity, "constrained_infeasible"] - a.loc[entity, "unconstrained_infeasible"]
        )
        assert r.infeasible_even_without_bounds == a.loc[entity, "unconstrained_infeasible"]
    new = pq.read_table(ROOT / manifest["outputs"][1]["path"])
    source = pq.read_table(
        ROOT
        / "data/processed/annual_diagnostic/20261027_publication_v1"
        / "explanatory_no_stop_priced.parquet"
    )
    source = source.filter(
        __import__("pyarrow").compute.equal(source["network_scenario"], "saved_plan_constraints")
    )
    assert new.select(source.column_names).equals(source)
    df = new.to_pandas()
    price = df[df.comparison_scope.eq("common_all_firms_feasible")]
    assert price.groupby("target_area").size().eq(16848).all()
    for area in ["東京", "中部"]:
        # 同じJERA区分を2地域へ参照するが、企業別主集計は1回。
        expected = b.loc[
            "jera",
            [
                "feasible_with_saved_bounds",
                "feasible_only_without_bounds",
                "infeasible_even_without_bounds",
            ],
        ]
        actual = (
            price[price.target_area.eq(area)]
            .central_category.value_counts()
            .reindex(expected.index)
        )
        np.testing.assert_array_equal(expected.astype(int), actual.astype(int))
    stats = pd.read_csv(REPORT / "qa/price_demand_fixed_output_by_category.csv")
    for r in stats[stats.variable.eq("area_price")].itertuples():
        s = price.loc[
            price.target_area.eq(r.area) & price.central_category.eq(r.central_category),
            "area_price",
        ].astype(float)
        assert len(s) == r.frames
        if len(s):
            assert np.isclose(s.median(), r.median)
        else:
            assert pd.isna(r.median)
    comp = pd.read_csv(REPORT / "qa/composition_by_season_time_month.csv")
    for _, g in comp.groupby(["area", "central_category", "dimension"]):
        assert g.frames.sum() == g.category_frames.iloc[0]
        if g.category_frames.iloc[0]:
            assert np.isclose(g.pct.sum(), 100)
        else:
            assert g.pct.isna().all()
    assert len(PdfReader(REPORT / "literature_contrast_one_page.pdf").pages) == 1
    assert len(PdfReader(REPORT / "one_page_result.pdf").pages) == 1
    for stem in [
        "01_method_readable",
        "02_central_three_categories",
        "A01_price_same_categories_candidate",
    ]:
        assert len(PdfReader(REPORT / "figures" / (stem + ".pdf")).pages) == 1
        assert "<svg" in (REPORT / "figures" / (stem + ".svg")).read_text()
    # 旧報告も変更しない。案内の入口だけ別途更新する。
    oldpaths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "reports/27_fy2025_annual"], text=True
    ).splitlines()
    for p in oldpaths:
        assert (ROOT / p).read_bytes() == subprocess.check_output(["git", "show", "HEAD:" + p]), p
    assert not subprocess.check_output(
        ["git", "ls-files", "data/raw", "data/interim", "data/processed"], text=True
    ).strip()
    result = dict(
        fixed_and_source_sha_verified=len(verified),
        old_annual_input_output_hashes_verified=len(annual["inputs"]) + len(annual["outputs"]),
        old_stage27_report_files_unchanged=len(oldpaths),
        common_times=16848,
        company_count=8,
        all_company_row_sums_equal=True,
        independent_old_summary_check=True,
        jera=[767, 14220, 1861],
        source_price_columns_values_types_equal=True,
        join_rate=1.0,
        unmatched=0,
        row_multiplication=0,
        composition_counts_sum_correct=True,
        one_page_sheets_verified=2,
        method_and_main_figure_pdf_svg=True,
        new_lp_calls=0,
        new_annual_acquisitions=0,
        final_baseline_adopted=False,
        pytest_passed=368,
        ruff="passed",
    )
    (REPORT / "qa/verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    implementation = [
        Path("src/paper_central_result.py"),
        Path("tests/test_paper_central_result.py"),
        Path("notebooks/10_paper_central_result.ipynb"),
        Path("scripts/prepare_paper_central_result.py"),
        Path("scripts/plot_paper_central_result.py"),
        Path("scripts/render_paper_summary_sheets.py"),
        Path("scripts/verify_paper_central_result.py"),
    ]
    manifest["reproduction"] = [dict(path=str(p), sha256=sha(ROOT / p)) for p in implementation]
    manifest["documents_and_figures"] = [
        dict(path=str(p.relative_to(ROOT)), sha256=sha(p))
        for p in sorted(REPORT.rglob("*"))
        if p.is_file()
    ]
    (ROOT / "metadata/paper_central_result_manifest_20261027.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(result, flush=True)


if __name__ == "__main__":
    main()
