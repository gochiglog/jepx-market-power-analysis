# 成果物を開く

[READMEへ戻る](../README.md)。すべてのパスは共有repo直下を基準とします。
ローカルのParquetを読むだけで確認できます。再取得や再計算は必要ありません。

## まず価格と需給計画を見る

保存場所：`data/processed/occto/demand_supply_direction/20261016_native_v1/`

| ファイル | 行数・列数 | 何が見られるか |
|---|---:|---|
| market_base_FY2025_with_reserve.parquet | 157,680行・161列 | 9エリアの価格・感応度と2断面の需給計画 |
| market_hjks_outage_58852_early_FY2025_with_reserve.parquet | 157,680行・279列 | 上記＋58,852行snapshot・早端の案A |
| market_hjks_outage_58852_late_FY2025_with_reserve.parquet | 同上 | 58,852行snapshot・遅端の案A |
| market_hjks_outage_7828_early_FY2025_with_reserve.parquet | 同上 | 7,828行snapshot・早端の案A |
| market_hjks_outage_7828_late_FY2025_with_reserve.parquet | 同上 | 7,828行snapshot・遅端の案A |

まず基本表を開いてください。主キーは`area, delivery_date, period`です。
案Aの4ファイルは代替条件であり、縦に足して4倍の市場観測として使いません。
案A列は`annotation__`で始まり、参照容量差は対象設備の部分和です。

[閲覧ノートブック](../notebooks/01_data_tour.ipynb)では、全13表の行数・列数・全列名と主要列の実データを個別に確認できます。

1. VS Codeでノートブックを開く。
2. カーネルにこのrepoの`.venv`（Python 3.12）を選ぶ。
3. 「すべて実行」すると、13表それぞれの主要列10行が表示されます。「全列名と型を開く」も利用できます。
4. 最後の「自由に閲覧」で、表・地域・受渡日・period・連系線名を指定し、「表示」を押します。
5. 「全列」で横スクロールし、「前のページ／次のページ」で別の行へ進めます。表示の切替は元データを変更しません。

日集計にはperiod、連系線表にはareaがありません。その表で使えない条件は空欄にします。
対話欄が使えない環境でも、続くコードセルの選択ID・条件を変更して全表を閲覧できます。
全行をメモリへ読み、VS Codeのデータビューアーで開くための例も末尾にあります。
表示用の抜粋と全件データを区別し、ファイル書込みや研究用集計は行いません。

| 主な列 | 読み方 |
|---|---|
| area_price | そのエリアの実績価格（円/kWh） |
| system_price_market | spot由来の実績system price。全国共通値の地域反復 |
| system_price_sensitivity_base | 感応度計算側の基準価格。spotで置換しない |
| reserve_nextday2__area_demand_forecast_mw | 翌々日の公表予測需要。実績需要ではない |
| reserve_latest__area_supply_plan_mw | 翌日・当日snapshotの公表供給力。実発電ではない |
| network_context_id | 連系線の元行集合を参照するID。地域輸入可能量ではない |

原列も残っているため列数が多くなっています。最初は上の列と時間キーに絞って表示すると流れをつかめます。

## 需要や連系線を別表で見る

同じ保存場所にあります。

| ファイル | 行数 | 粒度・注意 |
|---|---:|---|
| occto_demand_hourly_FY2025.parquet | 96,360 | 1時間×10エリア＋総計。総計と個別地域を加算しない |
| occto_demand_daily_energy_FY2025.parquet | 4,015 | 日電力量。日最大電力と別の量 |
| occto_demand_daily_peak_FY2025.parquet | 4,015 | 日最大電力。発生時刻を補っていない |
| occto_reserve_nextday2_FY2025.parquet | 175,200 | 翌々日の計画断面。沖縄を含む10エリア |
| occto_reserve_latest_FY2025.parquet | 175,200 | 翌日・当日の計画断面。実績値ではない |
| system_capacity_dayahead2_planned_with_directions_FY2025.parquet | 388,896 | 系統空容量。原符号と確認した方向を保持 |
| reserve_interconnector_nextday2_with_directions_FY2025.parquet | 209,520 | 予備率連系線・翌々日。方向保留も残す |
| reserve_interconnector_latest_with_directions_FY2025.parquet | 209,472 | 予備率連系線・翌日当日。実潮流と呼ばない |

需要1時間CSVは仕様説明に単位の相違があり、flag付きです。日計との小差268件も無修正で保持しています。
連系線はフェンスと構成設備が重なります。方向が付いたことだけを理由に地域の追加輸入能力へ足しません。
定義の詳細は[需要・需給の仕様](demand_supply_direction.md)、[変数辞書](../metadata/variable_dictionary.csv)へ。

## 設備まで遡る

| データ | 保存先 | 用途 |
|---|---|---|
| 案A固定版 | data/interim/occto/public_record_annotation/20261016_diagnostic_A_v1/ | 元8,468,544観測を保持し、公表記録を別列付与 |
| 参照容量差の固定版 | data/interim/occto/reference_capacity_gap/20261016_diagnostic_v1/ | 平均出力、参照容量差、対応・欠測等のflag |
| JEPX単独価格 | data/processed/jepx/spot/ | 17,520時刻の実績価格・入札量 |
| JEPX単独感応度 | data/processed/jepx/price_sensitivity/ | 17,520時刻の仮想価格・公表差分 |
| 原本 | data/raw/ | 取得した公式ファイル。そのまま保持 |

設備別の大きな表は全件を画面表示せず、必要な列を選んで読みます。
案Aの早端・遅端は復旧予定日の扱いを変えた診断シナリオです。主区分の差が0でも該当flagは変わります。
停止記録なしは「停止なし」ではありません。

## 版と出典を確認する

最新13表の出力SHA・サイズは[出力manifest](../metadata/demand_supply_direction_manifest_20261016.json)、
閲覧対象の行数・列数は[成果物一覧](../metadata/artifact_catalog.json)にあります。
以前の市場版は`data/processed/market/20261016_market_diagnostic_v1/`へ保持しています。
ファイル名の時刻や日付は版の識別です。新しい版が自動的に最終baselineへ昇格することはありません。

GitHubにはコード・説明・軽量QAだけを保存します。別の人に実データを渡す場合は、Gitとは別に共有が必要です。
