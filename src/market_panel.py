"""既存価格・案A・連系線を接続する診断用市場パネル。推定・補正はしない。"""

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.public_record_annotation import VERSIONS, read_market_profile
from src.time_normalization import fiscal_dates, sha256

TIME_KEY = ["delivery_date", "period"]
MARKET_KEY = ["area", *TIME_KEY]
AREAS = dict(
    zip(
        ["北海道", "東北", "東京", "中部", "北陸", "関西", "中国", "四国", "九州"],
        [
            "hokkaido",
            "tohoku",
            "tokyo",
            "chubu",
            "hokuriku",
            "kansai",
            "chugoku",
            "shikoku",
            "kyushu",
        ],
        strict=True,
    )
)
A_DIRECTORY = "data/interim/occto/public_record_annotation/20261016_diagnostic_A_v1"


def key_qa(frame, key, *, expected=None):
    if frame[key].isna().any().any() or frame.duplicated(key).any():
        raise ValueError(f"キーのnull/重複: {key}")
    if expected is not None and len(frame) != expected:
        raise ValueError(f"想定行数と不一致: {len(frame)} != {expected}")
    return dict(rows=len(frame), duplicate_keys=0, null_keys=0)


def checked_join(left, right, key, *, label, require_complete=True):
    """右側未使用キーも報告し、左行を維持。重複・未報告dropを許さない。"""
    key_qa(left, key)
    key_qa(right, key)
    if (set(left) & set(right)) - set(key):
        raise ValueError(f"結合列の衝突: {label}")
    merged = left.merge(right, how="left", on=key, validate="one_to_one", indicator=True)
    missing = int(merged._merge.eq("left_only").sum())
    unused = right[key].merge(left[key], on=key, how="left", indicator=True)
    unused = unused[unused._merge.eq("left_only")].drop(columns="_merge")
    qa = dict(
        label=label,
        left_rows=len(left),
        matched=len(left) - missing,
        unmatched=missing,
        match_rate=(len(left) - missing) / len(left),
        unused_right_rows=len(unused),
        row_growth=0,
        silent_drop=0,
    )
    if require_complete and missing:
        raise ValueError(f"{label}: 未対応{missing}行")
    return merged.drop(columns="_merge"), qa, unused


def build_prices(spot, sensitivity, *, year=2025):
    expected = len(fiscal_dates(year)) * 48
    for frame in (spot, sensitivity):
        key_qa(frame, TIME_KEY, expected=expected)
        if set(frame.delivery_date) != set(fiscal_dates(year)):
            raise ValueError("年度の日付集合が不一致")
        if (
            not frame.groupby("delivery_date")
            .period.apply(lambda p: set(p) == set(range(1, 49)))
            .all()
        ):
            raise ValueError("日内48コマ不一致")
    for col in ("timestamp_start", "timestamp_end", "timezone"):
        check = spot[TIME_KEY + [col]].merge(
            sensitivity[TIME_KEY + [col]], on=TIME_KEY, validate="one_to_one", suffixes=("_a", "_b")
        )
        if not check[col + "_a"].equals(check[col + "_b"]):
            raise ValueError("価格ソースの時間契約が異なる")
    # 重なる元列・出典は名前空間で保持。共通時間列も感応度側原列として残す。
    rename = {c: "sensitivity_source__" + c for c in sensitivity if c in spot and c not in TIME_KEY}
    national, qa, unused = checked_join(
        spot, sensitivity.rename(columns=rename), TIME_KEY, label="spot_sensitivity"
    )
    if len(unused):
        raise ValueError("価格キーの片側余剰")
    national["system_price_market"] = national.system_price
    national["baseline_price_match_flag"] = (
        national.system_price_market == national.system_price_sensitivity_base
    )
    mismatch = int((~national.baseline_price_match_flag).sum())
    frames = []
    for area, suffix in AREAS.items():
        frame = national.copy()
        frame["area"] = area
        frame["area_price"] = frame["area_price_" + suffix]
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    key_qa(result, MARKET_KEY, expected=9 * expected)
    return result, dict(
        price_join=qa,
        unique_time_keys=expected,
        known_baseline_mismatches=mismatch,
        matches=expected - mismatch,
        area_repeated_mismatches=9 * mismatch,
        sensitivity_column_renames=rename,
    )


def write_parquet(frame, path):
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, path, compression="zstd")
    reread = pq.read_table(path)
    if not table.equals(reread, check_metadata=True):
        raise ValueError(f"Parquet書戻し不一致: {path}")
    return dict(
        path=str(path),
        rows=len(frame),
        columns=len(frame.columns),
        sha256=sha256(path),
        nulls={c: int(frame[c].isna().sum()) for c in frame},
    )


def frozen_files(root, repo):
    frozen = json.loads(
        (repo / "metadata/research/public_record_annotation_frozen_20261016.json").read_text()
    )
    paths = []
    for group in (
        "outputs",
        "report_artifacts",
        "previous_interpretation_outputs",
        "frozen_base_files",
    ):
        for row in frozen.get(group, []):
            base = (
                repo
                if row.get("location") == "issue_worktree" or group == "report_artifacts"
                else root
            )
            p = base / row["path"]
            if sha256(p) != row["sha256"]:
                raise ValueError(f"固定版のSHA不一致: {p}")
            paths.append(dict(path=str(p), sha256=row["sha256"]))
    return paths


def snapshot_capacity_impact(root, report_dir):
    """変更セルの露出量と区分への純移管。容量の時点和はkW・コマ。"""
    columns = [
        "area",
        "delivery_date",
        "period",
        "capacity_reference_kw",
        "capacity_gap_reference_kw",
        "generation_kwh_30min",
    ]
    columns += [f"pubrec_{v}_{s}_state" for v in (58852, 7828) for s in ("early", "late")]
    source = pq.ParquetFile(
        root / A_DIRECTORY / "generation_with_snapshot_public_records_FY2025.parquet"
    )
    chunks, endpoint_differences = [], 0
    for batch in source.iter_batches(columns=columns, batch_size=131072):
        t = pa.Table.from_batches([batch])
        changed = pc.not_equal(t["pubrec_58852_early_state"], t["pubrec_7828_early_state"])
        for version in (58852, 7828):
            endpoint_differences += pc.sum(
                pc.cast(
                    pc.not_equal(
                        t[f"pubrec_{version}_early_state"], t[f"pubrec_{version}_late_state"]
                    ),
                    pa.int64(),
                )
            ).as_py()
        if pc.any(changed).as_py():
            chunks.append(t.filter(changed).to_pandas())
    changed = pd.concat(chunks, ignore_index=True)
    changed["zero_reference_gap_kw_cells"] = changed.capacity_gap_reference_kw.where(
        changed.generation_kwh_30min.eq(0)
    )
    metrics = dict(
        known_reference_capacity_kw_cells="capacity_reference_kw",
        reference_gap_kw_cells="capacity_gap_reference_kw",
        zero_reference_gap_kw_cells="zero_reference_gap_kw_cells",
        generation_kwh="generation_kwh_30min",
    )
    rows = []
    for area in [*AREAS, "沖縄"]:
        f = changed[changed.area.eq(area)]
        row = dict(
            area=area,
            changed_cells=len(f),
            known_capacity_cells=int(f.capacity_reference_kw.notna().sum()),
            calculable_gap_cells=int(f.capacity_gap_reference_kw.notna().sum()),
            zero_gap_cells=int(f.zero_reference_gap_kw_cells.notna().sum()),
        )
        row.update({k: f[v].sum(min_count=1) for k, v in metrics.items()})
        rows.append(row)
    impact = pd.DataFrame(rows)
    regional = read_market_profile(
        root / A_DIRECTORY / "market_join_by_snapshot_scenario.parquet",
        snapshot=VERSIONS[1],
        scenario="early",
    )
    denominators = regional.groupby("area", observed=True)[
        [
            "reference_capacity_known_kw",
            "reference_gap_partial_sum_kw",
            "zero_reference_capacity_kw",
            "observed_generation_kwh",
        ]
    ].sum(min_count=1)
    denominator_columns = {
        "known_reference_capacity_kw_cells": "reference_capacity_known_kw",
        "reference_gap_kw_cells": "reference_gap_partial_sum_kw",
        "zero_reference_gap_kw_cells": "zero_reference_capacity_kw",
        "generation_kwh": "observed_generation_kwh",
    }
    for metric, original in denominator_columns.items():
        impact[metric + "_region_total"] = impact.area.map(denominators[original])
        impact[metric + "_share_of_region_total"] = (
            pd.to_numeric(impact[metric]) / impact[metric + "_region_total"]
        )
    impact.to_csv(report_dir / "snapshot_state_capacity_impact_20261016.csv", index=False)
    transitions = []
    for (area, before, after), f in changed.groupby(
        ["area", "pubrec_58852_early_state", "pubrec_7828_early_state"], observed=True
    ):
        row = dict(area=area, state_58852=before, state_7828=after, changed_cells=len(f))
        row.update({k: f[v].sum(min_count=1) for k, v in metrics.items()})
        transitions.append(row)
    pd.DataFrame(transitions).to_csv(
        report_dir / "snapshot_state_transfers_20261016.csv", index=False
    )
    # 各移管は旧区分から減、新区分へ増。全体の差は0。未知量は未知のまま。
    net = []
    for r in transitions:
        for side, sign in (("state_58852", -1), ("state_7828", 1)):
            net.append(
                dict(
                    area=r["area"],
                    state=r[side],
                    cells=sign * r["changed_cells"],
                    **{k: None if pd.isna(r[k]) else sign * r[k] for k in metrics},
                )
            )
    net = pd.DataFrame(net).groupby(["area", "state"], observed=True).sum(min_count=1).reset_index()
    net.to_csv(report_dir / "snapshot_state_net_reallocation_20261016.csv", index=False)
    if net.cells.sum() != 0:
        raise ValueError("区分移管の件数が保存されない")
    return dict(
        changed_original_cells=len(changed),
        endpoint_main_state_differences=endpoint_differences,
        affected_generation_kwh=str(changed.generation_kwh_30min.sum(min_count=1)),
        total_capacity_and_gap_change=0,
        interpretation="同じ観測の区分移管。数量自体の改訂ではない",
    )


def run(root: Path, repo: Path, output: Path):
    if output.exists():
        raise ValueError("既存出力先への上書きは禁止")
    before = frozen_files(root, repo)
    output.mkdir(parents=True)
    spot_path = root / "data/processed/jepx/spot/jepx_spot_FY2025.parquet"
    sensitivity_path = (
        root / "data/processed/jepx/price_sensitivity/jepx_price_sensitivity_FY2025.parquet"
    )
    inputs = [dict(path=str(p), sha256=sha256(p)) for p in (spot_path, sensitivity_path)]
    panel, qa = build_prices(
        pq.read_table(spot_path).to_pandas(), pq.read_table(sensitivity_path).to_pandas()
    )
    from src.interconnector_market import build_network_contexts

    network, link_qa = build_network_contexts(root, output / "interconnectors")
    panel, network_join, unused = checked_join(
        panel,
        network[TIME_KEY + ["network_context_id"]].merge(
            pd.DataFrame({"area": list(AREAS)}), how="cross"
        ),
        MARKET_KEY,
        label="network_context",
    )
    if len(unused):
        raise ValueError("ネットワーク時刻キー余剰")
    qa["network_join"] = network_join
    qa["interconnectors"] = link_qa
    qa["base"] = write_parquet(panel, output / "market_base_FY2025.parquet")
    qa["profiles"] = []
    a_path = root / A_DIRECTORY / "market_join_by_snapshot_scenario.parquet"
    for snapshot in VERSIONS:
        for endpoint in ("early", "late"):
            region = read_market_profile(a_path, snapshot=snapshot, scenario=endpoint)
            # 型・共通時間列の同値検査後、元列を名前空間ごと保存する。
            r = region.rename(
                columns={c: "annotation__" + c for c in region if c not in MARKET_KEY}
            )
            joined, join, unused = checked_join(
                panel, r, MARKET_KEY, label=snapshot + "_" + endpoint
            )
            if len(unused) != 17520 or set(unused.area) != {"沖縄"}:
                raise ValueError("想定外の案A対象範囲差")
            for c in ("timestamp_start", "timestamp_end", "timezone"):
                if not (joined[c] == joined["annotation__" + c]).all():
                    raise ValueError("案A時間キー不整合")
            key_qa(joined, MARKET_KEY, expected=157680)
            info = write_parquet(joined, output / f"market_{snapshot}_{endpoint}_FY2025.parquet")
            info.update(
                join=join,
                intentional_unused_area="沖縄",
                intentional_unused_keys=len(unused),
                snapshot=snapshot,
                endpoint=endpoint,
            )
            qa["profiles"].append(info)
    qa["snapshot_capacity_impact"] = snapshot_capacity_impact(root, repo / "reports")
    after = frozen_files(root, repo)
    if before != after or any(sha256(Path(i["path"])) != i["sha256"] for i in inputs):
        raise ValueError("既存入力/固定版が変更された")
    qa.update(
        version="20261016_market_diagnostic_v1",
        baseline_selected=False,
        source_files_unchanged=True,
        frozen_files=before,
        price_inputs=inputs,
        final_headroom=False,
        national_series_repeated_by_area=True,
        demand_half_hour_interpolated=False,
        null_policy="元欠損と空区分nullを維持。自動補完しない",
    )
    files = [
        dict(path=str(p.relative_to(root)), sha256=sha256(p), size=p.stat().st_size)
        for p in sorted(output.rglob("*.parquet"))
    ]
    qa["outputs"] = files
    (repo / "reports/market_panel_FY2025_qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    return qa
