"""北海道の記述用散布図。台帳記載法人による参照群で、行使や余力を推定しない。"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.market_panel import checked_join, key_qa
from src.time_normalization import sha256

KEY = ["delivery_date", "period"]
UNIT_KEY = ["asset_key_candidate", *KEY]
VERSION = "20261016_hokkaido_scatter_v1"
A_PATH = Path("data/interim/occto/public_record_annotation/20261016_diagnostic_A_v1")
COMPARISON = Path("data/processed/comparison/20261016_decision_preparation_v2")
NATIVE = Path("data/processed/occto/demand_supply_direction/20261016_native_v1")
REPORT = Path("reports/13_hokkaido_scatter")
DECIMAL = pa.decimal128(38, 6)
CONDITIONS = ("58852_early", "58852_late", "7828_early", "7828_late")
LABEL = "制度対象外公開設備の参照容量差〔MW〕"


def nullable_sum(values):
    values = [v for v in values if pd.notna(v)]
    return sum(values, Decimal(0)) if values else None


def classify_assets(assets, registry, targets):
    """一意な点検済み設備＋個別法人確認だけ。未登録名を対象外にしない。"""
    key_qa(assets, ["asset_key_candidate"])
    key_qa(registry, ["hjks_business_name_raw", "market_area"])
    key_qa(targets, ["regulatory_entity", "market_area", "valid_from", "valid_to"])
    lookup = {(r.hjks_business_name_raw, r.market_area): r for r in registry.itertuples()}
    out = assets.copy()
    rows = []
    for a in assets.itertuples():
        name = a.hjks_business_name_unique_reference
        r = lookup.get((name, a.area))
        state, reason = "unknown", "法人名の個別確認なし/名称未一致"
        entity, url = None, None
        if a.hjks_business_name_reference_status != "scope_checked_unique_reference":
            reason = "点検済み設備への一意な台帳参照ではない"
        elif r is not None and str(r.name_identity_verified).lower() == "true":
            entity, url = r.canonical_legal_name, r.name_identity_source_url
            area_targets = targets[targets.market_area.eq(a.area)]
            covered = area_targets[
                (area_targets.valid_from <= "2025-04-01") & (area_targets.valid_to >= "2026-03-31")
            ]
            if len(covered) != len(area_targets) or not len(covered):
                reason = "FY2025のエリア制度対象期間を確認できない"
            elif r.valid_from > "2025-04-01" or r.valid_to < "2026-03-31":
                reason = "法人名称照合の診断適用期間を確認できない"
            else:
                listed = entity in set(covered.regulatory_entity)
                state = "target" if listed else "non_target"
                reason = (
                    "点検済み設備の記載名・公式法人名・FYエリア対象リストに一致"
                    if listed
                    else "個別に公式法人名を確認し完全なFYエリア対象リストと照合"
                )
        rows.append(
            dict(
                reference_classification=state,
                reference_classification_reason=reason,
                reference_canonical_legal_name=entity,
                reference_name_evidence_url=url,
                reference_classification_scope="HJKS記載法人のみ。所有/売電権/契約帰属ではない",
                final_supplier_classification_assigned=False,
            )
        )
    return pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def add_unit_reference(units, classified):
    key_qa(units, UNIT_KEY)
    # 台帳の全情報は別の設備表へ保存。365日配列等を30分行へ反復しない。
    extra = [
        c
        for c in classified
        if c not in units
        and (
            c.startswith("reference_")
            or c
            in (
                "hjks_business_source_field",
                "hjks_business_name_reference_status",
                "hjks_business_name_unique_reference",
                "final_supplier_classification_assigned",
            )
        )
    ]
    right = classified[["asset_key_candidate", *extra]]
    # 左はunit×時刻、右はunit。row数・順序・未一致を必ず検査する。
    out = units.merge(
        right,
        on="asset_key_candidate",
        how="left",
        validate="many_to_one",
        indicator=True,
        sort=False,
    )
    matched = out._merge.eq("both")
    qa = dict(
        left_rows=len(units),
        matched=int(matched.sum()),
        unmatched=int((~matched).sum()),
        match_rate=float(matched.mean()),
        row_growth=len(out) - len(units),
        silent_drop=0,
    )
    if len(out) != len(units) or not out.asset_key_candidate.equals(units.asset_key_candidate):
        raise ValueError("設備参照で行増殖/順序変更")
    if not matched.all():
        raise ValueError("設備参照の未一致")
    out = out.drop(columns="_merge")
    pd.testing.assert_frame_equal(out[units.columns], units, check_dtype=True)
    out["non_target_reference_gap_mw"] = [
        (capacity - energy / Decimal("0.5")) / Decimal(1000)
        if status == "non_target"
        and pd.notna(capacity)
        and capacity > 0
        and pd.notna(energy)
        and use == "use_diagnostic"
        and pd.notna(gap)
        else None
        for capacity, energy, status, use, gap in zip(
            out.capacity_reference_kw,
            out.generation_kwh_30min,
            out.reference_classification,
            out.research_use_status,
            out.capacity_gap_reference_kw,
            strict=True,
        )
    ]
    for calculated, original in zip(
        out.non_target_reference_gap_mw, out.capacity_gap_reference_kw, strict=True
    ):
        if pd.notna(calculated) and calculated * 1000 != original:
            raise ValueError("既存固定容量差と原単位からの計算が不一致")
    return out, qa


def aggregate_reference(units, *, expected_units, grouping=KEY):
    """部分和と欠測を同一キーで集計。全不能null、未知の0化なし。"""
    selected = units.reference_classification.eq("non_target")
    calculated = selected & units.non_target_reference_gap_mw.notna()
    capacity_held = (
        units.capacity_reference_kw.isna()
        | units.capacity_reference_kw.le(0)
        | units.research_use_status.ne("use_diagnostic")
    )
    for flag in ("qa_capacity_unavailable", "qa_period_contradiction", "qa_operation_boundary_day"):
        if flag in units:
            capacity_held |= units[flag].fillna(True)
    f = units[grouping].copy()
    f["R_ref_MW"] = units.non_target_reference_gap_mw.where(calculated)
    f["non_target_capacity_reference_mw"] = units.capacity_reference_kw.map(
        lambda v: v / 1000 if pd.notna(v) else None
    ).where(selected)
    f["non_target_calculated_capacity_mw"] = f.non_target_capacity_reference_mw.where(calculated)
    f["non_target_zero_output_gap_mw"] = f.R_ref_MW.where(units.generation_kwh_30min.eq(0))
    masks = {
        "non_target_observed_unit_count": selected,
        "non_target_calculated_unit_count": calculated,
        "non_target_output_missing_count": selected & units.generation_kwh_30min.isna(),
        "non_target_capacity_held_count": selected & capacity_held,
        "non_target_negative_gap_count": calculated & units.non_target_reference_gap_mw.lt(0),
        "classification_unknown_unit_count": units.reference_classification.eq("unknown"),
        "reference_target_unit_count": units.reference_classification.eq("target"),
    }
    for name, mask in masks.items():
        f[name] = mask.astype(int)
    f["observed_asset_set_id"] = units.asset_key_candidate.where(selected, "")
    f["calculated_asset_set_id"] = units.asset_key_candidate.where(calculated, "")
    quantities = [
        "R_ref_MW",
        "non_target_capacity_reference_mw",
        "non_target_calculated_capacity_mw",
        "non_target_zero_output_gap_mw",
    ]
    methods = {name: nullable_sum for name in quantities}
    methods.update({name: "sum" for name in masks})
    methods.update(
        {
            name: lambda ids: "|".join(sorted(v for v in ids if v))
            for name in ("observed_asset_set_id", "calculated_asset_set_id")
        }
    )
    result = f.groupby(grouping, sort=True, dropna=False).agg(methods).reset_index()
    expected = (
        pd.Series(expected_units, index=result.index)
        if grouping == KEY
        else result.non_target_observed_unit_count
    )
    result["non_target_expected_unit_count"] = expected
    result["non_target_unobserved_unit_count"] = (
        expected - result.non_target_observed_unit_count if grouping == KEY else None
    )
    result["reference_sum_is_partial"] = result.non_target_calculated_unit_count.ne(expected)
    result["R_ref_complete_MW"] = result.R_ref_MW.where(~result.reference_sum_is_partial, None)
    return result


def tied_quartiles(demand):
    """値の四分位点を使い同値を同群へ。重なる境界は畳み空群を報告する。"""
    x = pd.to_numeric(demand, errors="raise").astype(float)
    if np.isinf(x.dropna()).any():
        raise ValueError("需要に非有限値")
    valid = x.dropna()
    if valid.empty:
        return pd.Series(pd.NA, index=x.index, dtype="Int64"), dict(groups=0, boundaries=[])
    boundaries = sorted(set(float(v) for v in valid.quantile([0.25, 0.5, 0.75])))
    # 境界値そのものは下側へ入れる。値の順位で同値を分断しない。
    ids = np.searchsorted(boundaries, x.fillna(0).to_numpy(), side="left")
    occupied = sorted(set(ids[x.notna()]))
    compact = {old: i + 1 for i, old in enumerate(occupied)}
    labels = pd.Series(
        [compact.get(int(i)) if ok else pd.NA for i, ok in zip(ids, x.notna(), strict=True)],
        index=x.index,
        dtype="Int64",
    )
    rows = []
    for label, g in x.groupby(labels, dropna=True):
        rows.append(
            dict(
                group=int(label),
                count=len(g),
                min_demand_mw=float(g.min()),
                max_demand_mw=float(g.max()),
            )
        )
    return labels, dict(
        groups=len(rows),
        boundaries=boundaries,
        rule="境界以下を下側。同値は分割しない",
        rows=rows,
        null_count=int(x.isna().sum()),
    )


def select_import(network):
    direction = network.sending_area.eq("東北") & network.receiving_area.eq("北海道")
    result = network[direction].copy().reset_index(drop=True)
    if result.empty:
        raise ValueError("東北→北海道の確認済み方向なし")
    key_qa(result, KEY)
    if not result.direction_status.eq("official_orientation_verified").all():
        raise ValueError("方向未確認")
    if not result.source_variant.eq("system_capacity_dayahead2_planned").all():
        raise ValueError("空容量の計画断面が異なる")
    if result.link_name_raw.nunique() != 1:
        raise ValueError("複数設備を無条件に合算しない")
    return result


def connect_panel(market, aggregate, network):
    key_qa(market, KEY)
    out, q1, unused1 = checked_join(
        market, aggregate, KEY, label="non_target_reference", require_complete=False
    )
    if len(unused1):
        raise ValueError("市場にない参照群時刻")
    link = select_import(network)
    right = link.rename(columns={c: "import__" + c for c in link if c not in KEY})
    out, q2, unused2 = checked_join(
        out, right, KEY, label="tohoku_to_hokkaido", require_complete=False
    )
    for c in ("timestamp_start", "timestamp_end", "timezone"):
        matched = out["import__" + c].notna()
        if not out.loc[matched, c].equals(out.loc[matched, "import__" + c]):
            raise ValueError("連系線と市場の対象時刻不一致")
    out["import_capacity_direction_display_mw"] = (
        out.import__available_capacity_direction_display_mw
    )
    out["hokkaido_minus_tohoku_price"] = [
        h - t if pd.notna(h) and pd.notna(t) else None
        for h, t in zip(out.area_price, out.area_price_tohoku, strict=True)
    ]
    out["price_difference_sign"] = [
        "unknown" if pd.isna(v) else "positive" if v > 0 else "negative" if v < 0 else "zero"
        for v in out.hokkaido_minus_tohoku_price
    ]
    out["forecast_demand_group"], groups = tied_quartiles(
        out.reserve_nextday2__area_demand_forecast_mw
    )
    reasons = {
        "R_ref_missing": out.R_ref_MW.isna(),
        "price_missing": out.area_price.isna(),
        "import_display_missing": out.import_capacity_direction_display_mw.isna(),
        "forecast_demand_missing": out.forecast_demand_group.isna(),
    }
    out["main_plot_unavailable_reasons"] = [
        "|".join(k for k, v in reasons.items() if v.iloc[i]) for i in range(len(out))
    ]
    out["main_plot_eligible"] = out.main_plot_unavailable_reasons.eq("")
    out["supplementary_plot_eligible"] = (
        out.import_capacity_direction_display_mw.notna() & out.hokkaido_minus_tohoku_price.notna()
    )
    return out, dict(
        joins=[q1, q2],
        unused_network_keys=len(unused2),
        demand_groups=groups,
        main_plot_exclusion_reasons={k: int(v.sum()) for k, v in reasons.items()},
        main_plot_eligible=int(out.main_plot_eligible.sum()),
        supplementary_plot_eligible=int(out.supplementary_plot_eligible.sum()),
    )


def save_parquet(frame, path, *, original=None):
    fields = {f.name: f for f in original.schema} if original is not None else {}
    arrays, names = [], []
    for col in frame:
        kind = fields[col].type if col in fields else None
        if kind is None and (col.endswith("_mw") or col in ("R_ref_MW", "R_ref_complete_MW")):
            kind = DECIMAL
        array = pa.array(frame[col], type=kind, from_pandas=True)
        # 空のlist<null>もParquet標準の子名elementで型まで往復一致させる。
        if pa.types.is_list(array.type):
            array = array.cast(pa.list_(pa.field("element", array.type.value_type)))
        arrays.append(array)
        names.append(col)
    table = pa.Table.from_arrays(arrays, names=names)
    table = table.replace_schema_metadata(
        {b"version": VERSION.encode(), b"role": b"descriptive_only"}
    )
    if original is not None:
        if not table.select(original.column_names).equals(original, check_metadata=False):
            raise ValueError("元列全セル/型の保持に失敗")
    pq.write_table(table, path, compression="zstd")
    reread = pq.read_table(path)
    if not reread.equals(table, check_metadata=True):
        mismatches = [c for c in table.column_names if not reread[c].equals(table[c])]
        field_diffs = [
            f.name
            for f in table.schema
            if not f.equals(reread.schema.field(f.name), check_metadata=True)
        ]
        raise ValueError(
            f"Parquet書戻し不一致: {path.name}; cells={mismatches}; "
            f"fields={field_diffs}; metadata={reread.schema.metadata}"
        )
    return dict(path=str(path), rows=len(frame), columns=len(frame.columns), sha256=sha256(path))


def draw(panel, report):
    # 通常の描画ライブラリで共有可能なPNG/PDFを作る。回帰・集計線なし。
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib import pyplot as plt
    from matplotlib.colors import Normalize

    fonts = [
        f
        for f in font_manager.fontManager.ttflist
        if f.name in ("Hiragino Sans", "IPAexGothic", "Noto Sans CJK JP", "Arial Unicode MS")
    ]
    if not fonts:
        raise ValueError("日本語フォントがない。文字化けした図を保存しない")
    plt.rcParams.update({"font.family": fonts[0].name, "axes.unicode_minus": False})
    shown = panel[panel.main_plot_eligible]
    group_ids = sorted(shown.forecast_demand_group.dropna().unique())
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), sharex=True, sharey=True, layout="constrained")
    color = shown.import_capacity_direction_display_mw.astype(float)
    norm = Normalize(vmin=color.min(), vmax=color.max()) if len(color) else Normalize(0, 1)
    artist = None
    for ax, label in zip(axes.flat, group_ids, strict=False):
        g = shown[shown.forecast_demand_group.eq(label)]
        artist = ax.scatter(
            g.R_ref_MW.astype(float),
            g.area_price.astype(float),
            c=g.import_capacity_direction_display_mw.astype(float),
            cmap="viridis",
            norm=norm,
            s=7,
            alpha=0.65,
            linewidths=0,
            rasterized=True,
        )
        demand = g.reserve_nextday2__area_demand_forecast_mw
        ax.set_title(f"需要群{label}: {min(demand):,.0f}–{max(demand):,.0f} MW / {len(g):,}点")
        ax.grid(alpha=0.15)
    for ax in list(axes.flat)[len(group_ids) :]:
        ax.text(0.5, 0.5, "同値を分割しないため該当群なし", ha="center", transform=ax.transAxes)
    fig.supxlabel(LABEL)
    fig.supylabel("北海道spotエリア価格〔円/kWh〕")
    fig.suptitle(
        "北海道 FY2025：翌々日公表予測需要の群別・全時間\n"
        "台帳記載法人による公開設備参照群。稼働可能余力・因果効果ではない",
        fontsize=13,
    )
    if artist is not None:
        # 点は重なりを見せる半透明、カラーバーは不透明な同一スケール。
        from matplotlib.cm import ScalarMappable

        fig.colorbar(
            ScalarMappable(norm=norm, cmap="viridis"),
            ax=axes,
            label="東北→北海道 空容量・翌々日策定〔MW・方向別表示〕",
            shrink=0.82,
        )
    figures = []
    for ext in ("png", "pdf"):
        p = report / "figures" / f"hokkaido_reference_gap_scatter.{ext}"
        fig.savefig(p, dpi=180)
        figures.append(str(p))
    plt.close(fig)
    g = panel[panel.supplementary_plot_eligible]
    fig, ax = plt.subplots(figsize=(10, 6), layout="constrained")
    ax.scatter(
        g.import_capacity_direction_display_mw.astype(float),
        g.hokkaido_minus_tohoku_price.astype(float),
        s=7,
        alpha=0.35,
        linewidths=0,
        rasterized=True,
    )
    ax.axhline(0, color="gray", lw=0.8)
    ax.set(
        xlabel="東北→北海道 空容量・翌々日策定〔MW・方向別表示〕",
        ylabel="北海道価格 − 東北価格〔円/kWh〕",
        title=f"北海道 FY2025：方向別公表空容量と地域価格差（{len(g):,}点）\n"
        "価格差の正負を保持。物理的な分断・孤立の認定ではない",
    )
    ax.grid(alpha=0.15)
    for ext in ("png", "pdf"):
        p = report / "figures" / f"hokkaido_import_price_difference.{ext}"
        fig.savefig(p, dpi=180)
        figures.append(str(p))
    plt.close(fig)
    return dict(font=fonts[0].name, outputs=figures)


def contributions(units, count):
    selected = units[units.reference_classification.eq("non_target")].copy()
    fuel = aggregate_reference(selected, expected_units=count, grouping=[*KEY, "発電方式・燃種"])
    states = []
    for condition in CONDITIONS:
        col = "pubrec_" + condition + "_state"
        f = selected.copy()
        f["snapshot_public_record_state"] = f[col]
        grouped = aggregate_reference(
            f, expected_units=count, grouping=[*KEY, "snapshot_public_record_state"]
        )
        grouped["snapshot_endpoint_condition"] = condition
        states.append(grouped)
    return fuel, pd.concat(states, ignore_index=True)


def run(root, repo, output, *, report=None):
    if output.exists():
        raise ValueError("既存出力先は拒否。固定版を上書きしない")
    report = report or repo / REPORT
    if (report / "qa/qa.json").exists():
        raise ValueError("既存の記述版レポートを上書きしない。別版の実装・保存先を指定する")
    (report / "figures").mkdir(parents=True, exist_ok=True)
    (report / "qa").mkdir(parents=True, exist_ok=True)
    paths = [
        root / A_PATH / "generation_with_snapshot_public_records_FY2025.parquet",
        root / COMPARISON / "observed_unit_business_evidence_FY2025.parquet",
        root / COMPARISON / "system_capacity_dayahead2_planned_direction_comparison_FY2025.parquet",
        root / NATIVE / "market_base_FY2025_with_reserve.parquet",
        repo / "metadata/research/regulatory_targets.csv",
        repo / "metadata/research/hokkaido_business_reference_classification_20261016.csv",
    ]
    inputs = [dict(path=str(p), sha256=sha256(p)) for p in paths]
    units_arrow = pq.read_table(paths[0], filters=[("area", "=", "北海道")])
    units = units_arrow.to_pandas()
    assets = pq.read_table(paths[1], filters=[("area", "=", "北海道")]).to_pandas()
    targets = pd.read_csv(paths[4], keep_default_na=False)
    if targets.regulatory_entity.nunique() != 10 or len(targets) != 11:
        raise ValueError("制度対象の10法人11エリア行が変化")
    registry = pd.read_csv(paths[5], keep_default_na=False, dtype=str)
    classified = classify_assets(assets, registry, targets)
    annotated, unit_join = add_unit_reference(units, classified)
    count = int(classified.reference_classification.eq("non_target").sum())
    agg = aggregate_reference(annotated, expected_units=count)
    market_arrow = pq.read_table(paths[3], filters=[("area", "=", "北海道")])
    market = market_arrow.to_pandas()
    network = pq.read_table(
        paths[2], filters=[("sending_area", "=", "東北"), ("receiving_area", "=", "北海道")]
    ).to_pandas()
    panel, qa = connect_panel(market, agg, network)
    key_qa(panel, KEY, expected=17520)
    if (
        len(set(panel.delivery_date)) != 365
        or not panel.groupby("delivery_date")
        .period.apply(lambda s: set(s) == set(range(1, 49)))
        .all()
    ):
        raise ValueError("FY2025の365日×48コマが不完全")
    if min(panel.delivery_date) != date(2025, 4, 1) or max(panel.delivery_date) != date(
        2026, 3, 31
    ):
        raise ValueError("FY2025範囲不一致")
    fuel, states = contributions(annotated, count)
    for _, f in states.groupby("snapshot_endpoint_condition"):
        totals = f.groupby(KEY).R_ref_MW.agg(nullable_sum)
        expected = panel.set_index(KEY).R_ref_MW
        if not all(
            a == b or (pd.isna(a) and pd.isna(b)) for a, b in zip(totals, expected, strict=True)
        ):
            raise ValueError("公表区分の寄与が参照容量差へ加算一致しない")
    totals = fuel.groupby(KEY).R_ref_MW.agg(nullable_sum)
    if not totals.tolist() == panel.R_ref_MW.tolist():
        raise ValueError("燃種の寄与が参照容量差へ加算一致しない")
    output.mkdir(parents=True)
    outputs = []
    for frame, name, original in [
        (annotated, "hokkaido_unit_reference_FY2025.parquet", units_arrow),
        (classified, "hokkaido_asset_registry_FY2025.parquet", None),
        (panel, "hokkaido_scatter_panel_FY2025.parquet", market_arrow),
        (fuel, "hokkaido_reference_contribution_by_fuel_FY2025.parquet", None),
        (states, "hokkaido_reference_contribution_by_public_record_FY2025.parquet", None),
    ]:
        outputs.append(save_parquet(frame, output / name, original=original))
    figures = draw(panel, report)
    asset_cols = [
        "asset_key_candidate",
        "plant_code",
        "plant_name_raw",
        "unit_name_raw",
        "hjks_business_name_unique_reference",
        "reference_canonical_legal_name",
        "reference_classification",
        "reference_classification_reason",
        "reference_name_evidence_url",
        "diagnostic_mapping__capacity_reference_raw_kw",
        "generation_sum_kwh",
        "snapshot_source_file",
        "snapshot_source_sha256",
        "hjks_candidate_rows_json",
    ]
    classified[asset_cols].to_csv(report / "qa/asset_reference_registry.csv", index=False)
    pd.DataFrame(qa["demand_groups"]["rows"]).to_csv(
        report / "qa/forecast_demand_groups.csv", index=False
    )
    summary = {}
    for name, f in [("fuel", fuel), ("public_record", states)]:
        keys = (
            ["発電方式・燃種"]
            if name == "fuel"
            else ["snapshot_endpoint_condition", "snapshot_public_record_state"]
        )
        s = f.groupby(keys, dropna=False).agg(
            unit_slots=("non_target_observed_unit_count", "sum"),
            reference_gap_mw_slot_sum=("R_ref_MW", nullable_sum),
            zero_output_gap_mw_slot_sum=("non_target_zero_output_gap_mw", nullable_sum),
        )
        s.reset_index().to_csv(report / f"qa/contribution_{name}_summary.csv", index=False)
        summary[name] = len(s)
    # 月ごとの集合・欠測を保持し、変化を日付から追跡できるようにする。
    set_fields = [
        "non_target_observed_unit_count",
        "non_target_calculated_unit_count",
        "observed_asset_set_id",
        "calculated_asset_set_id",
        "non_target_output_missing_count",
        "non_target_capacity_held_count",
    ]
    changed = panel[set_fields].ne(panel[set_fields].shift()).any(axis=1)
    intervals = []
    for _, block in panel.groupby(changed.cumsum()):
        record = block[set_fields].iloc[0].to_dict()
        record.update(
            first_timestamp=block.timestamp_start.iloc[0],
            last_timestamp_end=block.timestamp_end.iloc[-1],
            slots=len(block),
        )
        intervals.append(record)
    pd.DataFrame(intervals).to_csv(report / "qa/asset_set_intervals.csv", index=False)
    panel.loc[~panel.main_plot_eligible, KEY + ["main_plot_unavailable_reasons"]].to_csv(
        report / "qa/unplottable_rows.csv", index=False
    )
    qa.update(
        version=VERSION,
        unit_join=unit_join,
        rows=len(panel),
        unit_rows=len(units),
        unit_labels=len(assets),
        classification_counts=classified.reference_classification.value_counts().to_dict(),
        scope_unit_count=count,
        distinct_observed_sets=panel.observed_asset_set_id.nunique(),
        distinct_calculated_sets=panel.calculated_asset_set_id.nunique(),
        numeric_summary={
            c: {
                "null_count": int(panel[c].isna().sum()),
                "unique_values": int(panel[c].nunique()),
                "min": str(panel[c].min()),
                "median": str(panel[c].median()),
                "max": str(panel[c].max()),
            }
            for c in (
                "R_ref_MW",
                "area_price",
                "import_capacity_direction_display_mw",
                "hokkaido_minus_tohoku_price",
            )
        },
        price_difference_sign_counts=panel.price_difference_sign.value_counts().to_dict(),
        negative_unit_cells=int((annotated.non_target_reference_gap_mw.dropna() < 0).sum()),
        output_missing_unit_cells=int(annotated.generation_kwh_30min.isna().sum()),
        unknown_classification_cells=int(annotated.reference_classification.eq("unknown").sum()),
        public_record_conditions=list(CONDITIONS),
        contribution_sum_checks="all_passed",
        original_unit_and_market_columns_unchanged=True,
        parquet_roundtrip="all_passed",
        official_split_result_column_available=False,
        figures=figures,
        inputs=inputs,
        outputs=outputs,
    )
    for row in inputs:
        if sha256(Path(row["path"])) != row["sha256"]:
            raise ValueError("入力固定版の変更")
    (report / "qa/qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    manifest = dict(
        version=VERSION,
        inputs=inputs,
        outputs=outputs,
        figure_sha256={str(p): sha256(p) for p in (report / "figures").iterdir()},
        scope="北海道の記述版。最終baseline・法人帰属・稼働可能余力ではない",
    )
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return qa
