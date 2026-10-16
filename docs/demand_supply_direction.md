# 需要・需給ETLと連系線方向：接続診断版

Issue #19、roadmap #9/#10。既存市場版 `20261016_market_diagnostic_v1` と案A `20261016_A_v1` は保持する。
今回の出力は共有repo `data/processed/occto/demand_supply_direction/20261016_native_v1/`。
採用対象は `metadata/demand_supply_direction_manifest_20261016.json` の13ファイル。
最終headroom・法人帰属・dominant/fringe・分析baselineは定義しない。

## 需要の元頻度

| 表 | 粒度 | 行数 | 数量列・単位 |
|---|---|---:|---|
| occto_demand_hourly_FY2025.parquet | 1時間×地域項目 | 96,360 | demand_hourly_csv_mwh、CSV仕様MWh |
| occto_demand_daily_energy_FY2025.parquet | 日×地域項目 | 4,015 | demand_daily_energy_mwh、MWh |
| occto_demand_daily_peak_FY2025.parquet | 日×地域項目 | 4,015 | demand_daily_peak_mw、MW |

地域項目は10個別エリアと別項目の「１０エリア計」。全ての原wide列も各行に保存する。
原9,490行×11項目＝104,390行。原行ID×areaで一意。総計と個別エリアを足さない。
需要にperiodを作らず、1時間は `hour_of_delivery` 0〜23とJST開始/終了を持つ。
日最大の発生時刻は不明のままで、日集計に仮の30分コマ/時刻を与えない。

CSV専用仕様表13-6は電力消費量MWh、一方画面仕様表13-5は1時間ごとの最大電力MWと記載する。
CSV専用定義のまま `demand_hourly_csv_mwh` に原数値を保持し、`qa_hourly_unit_display_conflict=True` を付ける。
MW、30分平均出力、瞬間最大と同一視しない。単位変換・補間・公表日量への置換は行わない。
[公式manual](https://www.occto.or.jp/assets/occtosystem2/files/250306_keitoujouhou.pdf)物理157〜159。
この相違はCSVの数値取得完了と別の意味確認事項であり、他の整備を停止しない。

## 30分計画需給と市場接続

翌々日 `reserve_nextday2` と翌日・当日 `reserve_latest` を各175,200行で保持する。
原ヘッダー・元セル・先頭更新情報・行出典を保存し、8数量列をDecimal128(38,6)で追加する。
需要/供給力/予備力はMW、予備率/使用率は%。翌日・当日も実現実績の表ではない。
`file_latest_updated_at` はCSV全体の更新日時。行更新・初回公表とは区別する。
原列の区分名***はFY2025の仕様であり、数量欠損とは別。数量の空欄/公式***は理由付きnull。
未知の非数値は停止し、黙ってNaNにしない。

既存市場の基本表と案A4条件それぞれに、2断面を `<variant>__` 接頭辞で追加した派生表を作る。
各157,680行、基本161列、案A条件付き279列。旧列を全セル保持し、one-to-oneを検証する。
沖縄17,520キー/断面は元の需給表へ保持し、市場範囲差として報告する。
同じブロック量が所属エリアに反復するため、ブロック量をエリア間で単純加算しない。
ブロックNoは時刻で変わる。予測需要を実績需要へ置換せず、1時間需要をこの表に複製しない。

## 連系線方向・範囲

既存3表の807,888原行をそれぞれ同じ行数・元列・型で保持し、方向/設備範囲の列を追加した別表を作る。
原市場の `network_context_id` →既存文脈表→`source_record_id` で新方向表も参照できる。
新表で単一原行が増殖しないことを検証する。
方向対応表は `metadata/research/interconnector_direction_scope_20261016.csv` の82行。
設備範囲の時点説明は `interconnector_scope_epochs_20261016.csv`。

系統表には `sending_area_set_json` / `receiving_area_set_json`、予備率表には順逆それぞれを
`forward__` / `reverse__` で追加する。1対多フェンスは地域集合のまま残す。
単一地域列nullは「複数地域集合」または「未確認」を表すので、direction_statusと集合を併読する。
公式の方向根拠と全FYの機器包含・有効期間確認を分け、後者は未確認flagを残す。
東/西関中フェンスはF1/F2、F3/F4の最小制約値。単純な別送電線ではなく、地域方向の直接対応を保留する。

負値の絶対値化・反転・ゼロ化、地域輸入能力への合算、flow/容量比からの制約認定はしない。
系統CSVの逆方向は全186,032負値が逆側であり、原列の符号付き容量式が全388,896行で整合する。
年次図の順正/逆負の説明と整合するが、正負だけで需給不足や実際の送電状態を認定しない。
予備率の方向別欄に系統CSVの符号処理を転用しない。

## 実行・不変性

```sh
python scripts/etl_demand_supply.py --data-root /path/to/shared-repo --output /path/to/new-directory
pytest -q
ruff check .
```

出力先が既存なら拒否する。開発時の途中出力 `20261016_v1/` はmanifestの採用対象に含めない。
raw203件・参考39件、既存固定34項目をSHAで照合。元列・原行・数値・Parquet再読込・時刻・joinを検査する。
新しい表もデータとしてGit管理外。元原本や固定版の上書き、欠測補完、行の自動除外は行わない。
