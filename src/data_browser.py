"""固定データを変更せず、英語標準名と日本語表示で読むための仮想ビュー。"""

import csv
import json
import re

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

AREA_NAMES = dict(
    zip(
        [
            "北海道",
            "東北",
            "東京",
            "中部",
            "北陸",
            "関西",
            "中国",
            "四国",
            "九州",
            "沖縄",
            "１０エリア計",
        ],
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
            "okinawa",
            "all_areas",
        ],
        strict=True,
    )
)
RAW_NAMES = {
    "受渡日": "delivery_date",
    "年月日": "date",
    "対象年月日": "target_date",
    "対象日": "target_date",
    "時刻コード": "period_code",
    "時刻": "time",
    "時間帯": "hour_interval",
    "更新日時": "updated_at",
    "エリア": "area",
    "エリア名": "area_name",
    "発電所コード": "plant_code",
    "発電所名": "plant_name",
    "ユニット名": "unit_name",
    "発電方式・燃種": "generation_method_fuel",
    "日量[kWh]": "daily_energy_kwh",
    "発電事業者": "generation_business_name",
    "発電形式": "generation_type",
    "認可出力": "authorized_capacity_kw",
    "認可出力（変更後）": "changed_authorized_capacity_kw",
    "適用開始日": "effective_date",
    "稼働開始日": "operation_start_date",
    "稼働終了日": "operation_end_date",
    "最終更新日時": "last_updated_at",
    "停止区分": "outage_category",
    "種別": "event_type",
    "低下量": "reduction_kw",
    "停止日時": "start_datetime",
    "復旧見通し": "recovery_outlook",
    "復旧予定日": "expected_recovery_date",
    "停止原因": "outage_reason",
    "システムプライス(円/kWh)": "spot_system_price",
    "システムプライス": "system_price",
    "売り入札量(kWh)": "sell_bid_volume_kwh",
    "買い入札量(kWh)": "buy_bid_volume_kwh",
    "約定総量(kWh)": "contracted_volume_kwh",
    "売りブロック入札総量(kWh)": "sell_block_bid_volume_kwh",
    "買いブロック入札総量(kWh)": "buy_block_bid_volume_kwh",
    "売りブロック約定総量(kWh)": "sell_block_contracted_volume_kwh",
    "買いブロック約定総量(kWh)": "buy_block_contracted_volume_kwh",
    "対象断面": "target_stage",
    "策定日": "planning_date",
    "策定／更新後": "planning_update_stage",
    "連系線": "link_name",
    "連系線名": "link_name",
    "方向": "direction",
    "空容量": "available_capacity",
    "計画潮流": "planned_flow",
    "広域調整枠": "wide_area_adjustment",
    "マージン": "margin",
    "運用容量": "operating_capacity",
    "運用容量決定要因": "operating_capacity_factor",
    "運用容量拡大分空容量": "expanded_available_capacity",
    "運用容量拡大分計画潮流": "expanded_planned_flow",
    "運用容量拡大分運用容量": "expanded_operating_capacity",
    "送電ＮＧ情報件数": "transmission_ng_count",
    "送電ＮＧ情報延べ容量": "transmission_ng_capacity",
    "最新更新年月日時刻": "latest_updated_at",
    "ブロックNo": "block_number",
    "区分名": "category_name",
    "広域ブロック需要(MW)": "block_demand_mw",
    "広域ブロック供給力(MW)": "block_supply_mw",
    "広域ブロック予備力(MW)": "block_reserve_mw",
    "広域予備率(%)": "wide_area_reserve_pct",
    "広域使用率(%)": "wide_area_usage_pct",
    "エリア需要(MW)": "area_demand_mw",
    "エリア供給力(MW)": "area_supply_mw",
    "エリア予備力(MW)": "area_reserve_mw",
    "移動供給力(MW)": "transfer_supply_mw",
}
for area, eng in AREA_NAMES.items():
    RAW_NAMES[area] = "demand_" + eng
    RAW_NAMES[f"エリアプライス{area}(円/kWh)"] = "area_price_" + eng
for side, jp in [("forward", "順方向"), ("reverse", "逆方向")]:
    for name, eng in [
        ("マージン(MW)", "margin_mw"),
        ("予想潮流(MW)", "forecast_flow_mw"),
        ("分断情報", "split_information"),
        ("移動後空容量(MW)", "post_transfer_available_capacity_mw"),
        ("空容量(MW)", "available_capacity_mw"),
        ("計画潮流(MW)", "planned_flow_mw"),
        ("運用容量(MW)", "operating_capacity_mw"),
    ]:
        RAW_NAMES[jp + name] = side + "_" + eng
for side, jp in [("sell", "売"), ("buy", "買")]:
    for mw in [500, 1000, 5000]:
        RAW_NAMES[f"{jp}{mw}MW"] = f"{side}_{mw}mw"

LABELS = {
    "area": "エリア",
    "delivery_date": "受渡日",
    "period": "30分コマ番号",
    "timestamp_start": "区間開始",
    "timestamp_end": "区間終了",
    "timezone": "タイムゾーン",
    "area_price": "当該エリアのスポット価格",
    "system_price_market": "スポット実績システム価格",
    "system_price": "スポット実績システム価格",
    "system_price_sensitivity_base": "価格感応度の計算用基準価格",
    "baseline_price_match_flag": "実績価格と感応度基準の照合結果（監査）",
    "link_name_raw": "連系線・設備群の名称",
    "direction_raw": "原資料の方向",
    "available_capacity_mw": "空容量（原方向の符号）",
    "operating_capacity_mw": "運用容量（原方向の符号）",
    "planned_flow_mw": "計画潮流（原方向の符号）",
    "margin_mw": "マージン（原方向の符号）",
    "available_capacity_direction_display_mw": "空容量（確認済み方向別表示）",
    "sending_area_set_json": "送出地域",
    "receiving_area_set_json": "受入地域",
    "sending_area": "送出地域",
    "receiving_area": "受入地域",
    "direction_status": "方向の確認状態",
    "directional_geography_status": "地域対応の確認状態",
    "source_file": "元ファイル",
    "source_row": "元ファイル行番号",
    "source_sha256": "元ファイルのSHA256",
    "source_row_number": "元行番号",
    "row_updated_at": "原行の更新日時",
    "file_latest_updated_at": "ファイル全体の最新更新日時",
    "initial_publication_datetime_known": "初回公表日時を確認できるか",
    "area_demand_forecast_mw": "エリアの公表予測需要",
    "area_supply_plan_mw": "エリアの公表計画供給力",
    "area_reserve_published_mw": "エリアの公表予備力",
    "wide_area_reserve_rate_published_pct": "広域予備率（公表値）",
    "reserve_information_stage": "需給情報の計画断面",
    "block_number_raw": "広域ブロック番号",
    "demand_hourly_csv_mwh": "1時間需要（CSV仕様の単位・定義相違は詳細へ）",
    "demand_daily_energy_mwh": "日電力量",
    "demand_daily_peak_mw": "日最大電力",
    "hour_of_delivery": "時間帯の始端（0〜23時）",
    "area_scope": "地域値・総計の区分",
    "plant_code": "発電所コード",
    "plant_name": "発電所名",
    "unit_name": "ユニット名",
    "plant_name_raw": "発電所名（原表記）",
    "unit_name_raw": "ユニット名（原表記）",
    "generation_kwh_30min": "30分発電電力量",
    "generation_avg_kw": "30分平均発電出力",
    "authorized_capacity_kw": "認可出力（台帳記載）",
    "changed_authorized_capacity_kw": "変更後認可出力",
    "operation_start_date": "稼働開始日（台帳記載）",
    "operation_end_date": "稼働終了日（台帳記載）",
    "start_datetime": "停止・低下の開始日時",
    "expected_recovery_date": "復旧予定日（時刻なし）",
    "expected_recovery_datetime": "復旧予定日時（未確認なら欠測）",
    "actual_recovery_datetime": "復旧実績日時（未確認なら欠測）",
    "reduction_kw": "公表平均低下量（各30分の損失ではない）",
    "outage_type": "公表停止・低下の種類",
    "publication_datetime": "初回公表日時（未確認なら欠測）",
    "update_datetime": "更新日時",
    "snapshot_row_id": "台帳・停止取得版の元行ID",
    "target_snapshot_row_id": "参照先台帳の元行ID",
    "capacity_reference_source_file": "容量参照元ファイル",
    "capacity_reference_source_sha256": "容量参照元のSHA256",
    "capacity_snapshot_download_time_known": "台帳の実取得日時が判明しているか",
    "block_component_check": "ブロックと構成機器の非重複検査",
    "capacity_reference_kw": "参照台帳の容量",
    "capacity_gap_reference_kw": "参照容量と平均発電出力の差",
    "r_ref_mw": "制度対象外公開設備の参照容量差",
    "hjks_business_name_unique_reference": "一意に参照できるHJKS記載事業者",
    "reference_classification": "台帳記載法人の制度リスト照合（最終分類ではない）",
}
STATE_NAMES = {
    "mapping_unavailable": "設備対応ができず照合不可",
    "held_asset_period": "設備の期間を確認できず保留",
    "held_invalid_dates": "日時不正で保留",
    "held_unknown_recovery": "復旧予定不明で保留",
    "held_update_candidates": "更新候補があり保留",
    "held_multiple_records": "複数記録があり保留",
    "held_date_boundary": "日付境界が不明で保留",
    "matched_planned_stop": "計画停止記録の予定区間に該当",
    "matched_unplanned_stop": "計画外停止記録の予定区間に該当",
    "matched_reduction": "出力低下記録の予定区間に該当",
    "matched_other": "その他記録に該当",
    "no_applicable_record": "該当記録なし（停止なしではない）",
}


def canonical_name(name, *, all_columns=()):
    """値は変更せず、原列にはraw名前空間を付ける。未知の日本語は停止する。"""
    if name == "system_price":
        return (
            "source__system_price"
            if "system_price_market" in all_columns
            else "system_price_market"
        )
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        return name.lower()
    pieces = name.split("__")
    leaf = pieces[-1]
    if leaf not in RAW_NAMES:
        raise ValueError(f"標準英語名が未定義: {name}")
    prefix = "__".join(pieces[:-1])
    return (prefix + "__" if prefix else "") + "raw__" + RAW_NAMES[leaf]


def metadata_definitions(root, path):
    result = {}
    with (root / "metadata/variable_dictionary.csv").open() as stream:
        variables = list(csv.DictReader(stream))
    for row in variables:
        result.setdefault(row["variable_name"], (row["definition"], row["unit"]))
    source_id = None
    if path.name.startswith("hjks_unit_"):
        source_id = "hjks_unit"
    elif path.name.startswith("hjks_outage_"):
        source_id = "hjks_outage"
    elif path.name == "generation_long_FY2025.parquet":
        source_id = "occto_generation"
    elif path.name == "jepx_spot_FY2025.parquet":
        source_id = "jepx_spot"
    if source_id:
        for row in variables:
            if row["source_id"] == source_id:
                result[row["variable_name"]] = (row["definition"], row["unit"])
    # 同名のvalid_to等でも台帳と規制一覧では意味が異なる。表別の根拠を優先する。
    for filename, table_key, column_key, definition_key in [
        ("demand_supply_direction_column_lineage.csv", "table", "column", "definition"),
        ("decision_preparation_lineage.csv", "output_file", "column", "definition_ja"),
        ("market_panel_column_lineage.csv", "output_table", "output_column", "definition"),
    ]:
        with (root / "metadata" / filename).open() as stream:
            for row in csv.DictReader(stream):
                if row[table_key].rsplit("/", 1)[-1] == path.name:
                    result[row[column_key]] = (row[definition_key], row["unit"])
    result.update(
        {
            "target_snapshot_row_id": (
                "対応候補が参照する台帳取得版の元行ID。正式mappingの確定ではない。",
                "not_applicable",
            ),
            "capacity_reference_source_file": (
                "診断で容量を参照した台帳CSVのパス。",
                "not_applicable",
            ),
            "capacity_reference_source_sha256": ("容量参照元台帳のSHA256。", "not_applicable"),
            "capacity_snapshot_download_time_known": (
                "台帳の実取得日時が分かるか。登録日時と区別する。",
                "not_applicable",
            ),
            "block_component_check": (
                "西名古屋7-2のブロックと構成機器の重複参照を防ぐ検査結果。",
                "not_applicable",
            ),
        }
    )
    return result


def column_catalog(path, root, table_id):
    fields = pq.read_schema(path)
    definitions = metadata_definitions(root, path)
    rows = []
    for i, f in enumerate(fields):
        name = canonical_name(f.name, all_columns=fields.names)
        parts = f.name.split("__")
        leaf = parts[-1]
        unit = definitions.get(f.name, definitions.get(leaf, ("", "not_applicable")))[1]
        definition = definitions.get(f.name, definitions.get(leaf, ("", unit)))[0]
        raw = "raw__" in name
        if raw:
            label = leaf + "（原表記）"
            definition = (
                "取得元の列を型と原値のまま保持。計算用数値が別にある場合は通常表示でそちらを参照。"
            )
        else:
            label = LABELS.get(f.name, LABELS.get(leaf, LABELS.get(name, "")))
            if not label:
                for state, jp in STATE_NAMES.items():
                    if state in f.name:
                        label = jp + "：" + (definition.split("：")[0] if definition else leaf)
                        break
            if not label:
                label = (
                    definition.split("：")[0] if definition else "詳細情報（元名：" + f.name + "）"
                )
            if len(label) > 65:
                label = label[:62] + "…"
        if "reserve_nextday2__" in f.name:
            label = "翌々日：" + label
        elif "reserve_latest__" in f.name:
            label = "翌日・当日：" + label
        if f.name.startswith("halfhour_1__"):
            label = "前半30分：" + label
        elif f.name.startswith("halfhour_2__"):
            label = "後半30分：" + label
        if unit in ("not_applicable", "TODO"):
            if name.endswith(("_mw", "_kw", "_kwh", "_mwh")) and not raw:
                unit = {"mw": "MW", "kw": "kW", "kwh": "kWh", "mwh": "MWh"}[name.rsplit("_", 1)[1]]
        group = "原本表記" if raw else "正規化・派生列"
        if any(
            t in name for t in ("source_", "sha256", "context_id", "qa_", "verification", "status")
        ):
            group = "出典・監査"
        rows.append(
            dict(
                table_id=table_id,
                ordinal=i,
                standard_english_name=name,
                japanese_name=label,
                unit=unit,
                arrow_type=str(f.type),
                column_group=group,
                source_column=f.name,
                definition_ja=definition or "出典・処理の詳細列。元列名・型を保持して閲覧する。",
            )
        )
    if len({r["standard_english_name"] for r in rows}) != len(rows):
        raise ValueError(f"標準列名の衝突: {path}")
    return rows


def table_registry(root):
    items = json.loads((root / "metadata/artifact_catalog.json").read_text())["artifacts"]
    paths = [x["path"] for x in items]
    paths += ["data/interim/occto/generation/generation_long_FY2025.parquet"]
    paths += [
        str(p.relative_to(root))
        for p in sorted((root / "data/interim/hjks/diagnostics").glob("*.parquet"))
    ]
    result = []
    titles = {
        "market_base_FY2025_with_reserve": "基本市場表（地域価格・実績system価格・計画需給）",
        "jepx_spot_FY2025": "JEPXスポット市場・価格と入札量",
        "jepx_price_sensitivity_FY2025": "JEPX価格感応度・仮想価格と公表差分",
        "occto_demand_hourly_FY2025": "需要実績：1時間（CSV定義）",
        "occto_demand_daily_energy_FY2025": "需要実績：日電力量",
        "occto_demand_daily_peak_FY2025": "需要実績：日最大電力",
        "occto_reserve_nextday2_FY2025": "計画需給：翌々日",
        "occto_reserve_latest_FY2025": "計画需給：翌日・当日",
        "system_capacity_dayahead2_planned_with_directions_FY2025": (
            "連系線：系統空容量・翌々日策定"
        ),
        "reserve_interconnector_nextday2_with_directions_FY2025": "連系線：予備率公表・翌々日",
        "reserve_interconnector_latest_with_directions_FY2025": "連系線：予備率公表・翌日当日",
        "generation_long_FY2025": "発電実績：ユニット×30分",
    }
    for index, path in enumerate(paths):
        p = root / path
        stem = p.stem
        role = (
            "基本台帳" if stem in titles or "/hjks/diagnostics/" in path else "比較・診断（詳細）"
        )
        title = titles.get(
            stem,
            {
                "generation_with_snapshot_public_records_FY2025": "公表記録付き発電実績（診断）",
                "reference_capacity_gap_FY2025": "参照容量と平均発電出力の差（診断版）",
                "market_hourly_price_demand_FY2025": "1時間比較表：価格の時間平均と需要",
                "system_capacity_dayahead2_planned_direction_comparison_FY2025": "空容量・価格差",
                "reserve_interconnector_nextday2_direction_comparison_FY2025": "連系線：翌々日比較",
                "reserve_interconnector_latest_direction_comparison_FY2025": "連系線：翌日当日比較",
                "observed_unit_business_evidence_FY2025": "観測ユニットと台帳記載事業者の参照根拠",
                "hjks_business_candidate_rows_FY2025": "台帳記載事業者の候補行",
                "business_regulatory_name_comparison_FY2025": "事業者名と制度対象名の照合候補",
                "hokkaido_unit_reference_FY2025": "北海道：ユニット別参照指標",
                "hokkaido_asset_registry_FY2025": "北海道：対象設備・台帳記載法人",
                "hokkaido_scatter_panel_FY2025": "北海道：散布図用30分表",
                "hokkaido_reference_contribution_by_fuel_FY2025": "北海道：参照容量差の燃種別内訳",
                "hokkaido_reference_contribution_by_public_record_FY2025": "北海道：公表記録別内訳",
            }.get(stem, stem),
        )
        if "/hjks/diagnostics/" in path:
            if "unit_677" in stem:
                title = "ユニット台帳：終了設備を含む手動取得版（677行）"
            elif "unit_529" in stem:
                title = "ユニット台帳：別取得版（529行）"
            elif "outage_7828" in stem:
                title = "停止公表記録：FY2025指定の手動取得版（7,828行）"
            else:
                title = "停止公表記録：期間指定なしの取得版（58,852行）"
        if "market_hjks_outage" in stem:
            n = "7,828" if "_7828_" in stem else "58,852"
            end = "復旧予定日の始めまで" if "_early_" in stem else "復旧予定日の終わりまで"
            title = f"停止公表記録付き市場表：{n}行の取得版／{end}（比較条件）"
        pf = pq.ParquetFile(p)
        result.append(
            dict(
                table_id=f"t{index + 1:02d}",
                title_ja=title,
                category=role,
                source_path=path,
                rows=pf.metadata.num_rows,
                columns=len(pf.schema_arrow),
            )
        )
    return result


def primary_columns(columns):
    """通常画面は計算用の主要列。原列・感応度基準・QAは詳細へ。"""
    wanted = [
        "area",
        "delivery_date",
        "period",
        "hour_of_delivery",
        "timestamp_start",
        "timestamp_end",
        "plant_code",
        "plant_name",
        "unit_name",
        "area_price",
        "system_price_market",
        "sell_bid_volume",
        "buy_bid_volume",
        "contracted_volume",
        "demand_hourly_csv_mwh",
        "demand_daily_energy_mwh",
        "demand_daily_peak_mw",
        "area_demand_forecast_mw",
        "area_supply_plan_mw",
        "area_reserve_published_mw",
        "wide_area_reserve_rate_published_pct",
        "link_name_raw",
        "direction_raw",
        "sending_area_set_json",
        "receiving_area_set_json",
        "available_capacity_mw",
        "operating_capacity_mw",
        "planned_flow_mw",
        "direction_status",
        "generation_kwh_30min",
        "raw__generation_business_name",
        "raw__generation_method_fuel",
        "authorized_capacity_kw",
        "start_datetime",
        "expected_recovery_date",
        "reduction_kw",
        "outage_type",
        "r_ref_mw",
    ]
    if "area_price" not in columns:
        wanted += [c for c in columns if c.startswith("area_price_")]
    wanted += [
        c
        for c in columns
        if c.startswith(
            (
                "virtual_price_",
                "published_difference_",
                "forward_available_",
                "reverse_available_",
                "reserve_nextday2__area_demand",
                "reserve_latest__area_supply",
            )
        )
    ]
    result = [c for c in dict.fromkeys(wanted) if c in columns]
    if not result:
        result = [c for c in columns if "raw__" not in c and "sensitivity_base" not in c][:12]
    return result


def read_page(root, table, catalog, *, limit=20, offset=0, filters=None, detail=False):
    if offset < 0 or limit < 1 or limit > 500:
        raise ValueError("表示件数は1〜500、開始行は0以上")
    mapping = {r["standard_english_name"]: r["source_column"] for r in catalog}
    selected = list(mapping) if detail else primary_columns(mapping)
    source = ds.dataset(root / table["source_path"], format="parquet")
    expression = None
    for key, value in (filters or {}).items():
        if value is None or value == "":
            continue
        if key not in mapping:
            raise ValueError("この表には条件列がありません: " + key)
        actual = mapping[key]
        scalar = pa.scalar(value, type=source.schema.field(actual).type)
        term = ds.field(actual) == scalar
        expression = term if expression is None else expression & term
    matched = source.count_rows(filter=expression)
    pieces = []
    skipped = 0
    remaining = limit
    for batch in source.to_batches(
        columns=[mapping[c] for c in selected], filter=expression, batch_size=2048
    ):
        if skipped + len(batch) <= offset:
            skipped += len(batch)
            continue
        begin = max(0, offset - skipped)
        part = batch.slice(begin, min(remaining, len(batch) - begin))
        pieces.append(part)
        remaining -= len(part)
        skipped += len(batch)
        if not remaining:
            break
    schema = pa.schema([source.schema.field(mapping[c]) for c in selected])
    original = pa.Table.from_batches(pieces, schema=schema)
    result = original.rename_columns(selected)
    return result, dict(
        matched_rows=matched,
        shown_rows=len(result),
        offset=offset,
        source_rows_unchanged=True,
        values_and_types_unchanged=True,
    )


def japanese_display(table, catalog):
    """表示だけを日本語にする。返す英語ビュー・元データの値は変更しない。"""
    info = {r["standard_english_name"]: r for r in catalog}
    result = table.to_pandas()
    for c in result:
        if c.endswith(("sending_area_set_json", "receiving_area_set_json")):
            result[c] = result[c].map(
                lambda v: "・".join(json.loads(v)) if isinstance(v, str) else v
            )
        if c.endswith("direction_status"):
            result[c] = result[c].replace(
                {
                    "official_orientation_verified": "公式資料で方向確認済み",
                    "manual_review": "要確認",
                }
            )
    labels = []
    for c in result:
        r = info[c]
        unit = r["unit"]
        label = r["japanese_name"]
        if unit not in ("TODO", "not_applicable"):
            label += "〔" + unit + "〕"
        if label in labels:
            label += "（" + c + "）"
        labels.append(label)
    result.columns = labels
    return result
