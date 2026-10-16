"""固定した参照容量差のゼロ/欠測/大幅超過を診断する。補正はしない。"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.gap_interpretation import (  # noqa: E402
    check_frozen,
    grouped_runs_and_missing,
    inspect_raw_cases,
    load_fixed_cells,
    nuclear_stats,
    zero_diagnosis,
)
from src.outage_case_studies import make_cases  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports"
    frozen = check_frozen(args.data_root, repo)
    args.output.mkdir(parents=True, exist_ok=False)
    with (reports / "reference_gap_analysis_mapping_20261016.csv").open() as f:
        mapping = list(csv.DictReader(f))
    source = args.data_root / (
        "data/interim/occto/reference_capacity_gap/20261016_diagnostic_v1/"
        "reference_capacity_gap_FY2025.parquet"
    )
    df, fuels = load_fixed_cells(source, mapping)
    if df.duplicated(["aid", "slot"]).any():
        raise ValueError("元unit×コマ重複")
    df.to_parquet(args.output / "observed_cells_diagnostic.parquet", index=False)
    (args.output / "fuels.json").write_text(json.dumps(fuels, ensure_ascii=False, indent=2))
    states, runs, kansai, absent = zero_diagnosis(df, mapping, fuels, reports, args.output)
    grouped_runs_and_missing(df, mapping, runs, fuels, reports)
    spikes = inspect_raw_cases(args.data_root, repo, df, mapping, reports)
    nuclear = nuclear_stats(df, mapping, reports)
    cases_qa = make_cases(args.data_root, repo, mapping, df, args.output)
    check_frozen(args.data_root, repo)
    qa = dict(
        diagnostic_only=True,
        frozen_files_verified=len(frozen["files"]),
        cells=len(df),
        calculable_cells=int(df.calculable.sum()),
        observed_zero_cells=int(df.zero.sum()),
        calculable_zero_cells=int((df.zero & df.calculable).sum()),
        generation_missing_cells=int(df.missing.sum()),
        negative_gap_cells=int((df.gap < 0).sum()),
        state_counts=dict(Counter(r["state"] for r in states)),
        zero_run_count=len(runs),
        longest_zero_run_cells=max(r["cells"] for r in runs),
        kansai_raw_missing=sum(r["raw_empty_cells"] for r in kansai),
        kansai_absent_unit_days_within_observed_span=sum(r["absent_unit_days"] for r in absent),
        top5_daily_sum_mismatches=sum(not r["daily_sum_matches"] for r in spikes),
        nuclear_negative_cells=sum(r["negative_gap_cells"] for r in nuclear),
        dropped_rows=0,
        duplicate_key=0,
        outage_case_qa=cases_qa,
        output=str(args.output),
        statistical_dtype="float64集計。原Decimalの固定版は変更せず保持",
    )
    (reports / "reference_gap_interpretation_qa_20261016.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(qa, ensure_ascii=False))
