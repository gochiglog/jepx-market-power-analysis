# 生データ収集の実行手順

## 手順と範囲

各ソースの取得方法は `metadata/data_sources.yml` の `acquisition` に記録しています。
取得は公式フォームと同じ処理を使用します。認証回避やTLS検証無効化はしません。
HJKSだけはこの環境のPython TLSで接続できず、証明書検証を有効にしたOS標準curlを使います。
コードはmacOS/Linux用です（manifestの排他ロックにfcntl、rawの原子的保存にhard linkを使用）。

```sh
uv sync --locked
# 種類ごとに、まず1件取得して形式・文字コード・BOM・列名・対象日を確認
uv run python scripts/acquire_raw.py --variant spot --phase sample
uv run python scripts/acquire_raw.py --variant generation --phase sample
# 同じ種類の有効なサンプルがmanifestに存在する場合のみ月単位取得を許可
uv run python scripts/acquire_raw.py --variant generation --phase full
# 保存済みファイルのSHA256、manifest、収録日を読み取り専用で監査
uv run python scripts/audit_raw.py --output reports/raw_acquisition_summary.json
uv run python scripts/validate_metadata.py
uv run pytest
uv run ruff check .
```

対応するvariantは `spot`、`sensitivity`、`sensitivity_diff`、`generation`、`unit`、`outage`、
`demand`、`reserve_nextday2`、`reserve_latest`、`reserve_interconnector_nextday2`、
`reserve_interconnector_latest`、`regulatory`、`high_price` です。

JEPXのサンプルは年度ファイル1件です。FY2025の全期間が収録されていれば、それを保持し、
full実行でも再取得しません。その他の時系列ファイルは1日サンプルの後、12か月を取得します。
予備率・連系線の公式最大31日制限を守ります。失敗した種類はその場で停止してmanifestに記録し、
取得不能範囲を隠して続行しません。HJKSの現在版や未検査PDFにはfull取得を許可しません。

現在の公式需要実績CSVは**1時間粒度**です。`demand` はその元ファイルを保存するだけで、
30分への変換は行いません。利用方法は研究判断が必要です。

## 保存・manifest

新規取得は整理済みの `data/raw/jepx/`、`occto/`、`hjks/`、`egc/` 以下へ種類別に保存します。元バイト列を復号・再エンコードせず、
manifestにHTTP URL/最終URL、HTTP method、取得条件、元ファイル名、取得日時（Asia/Tokyo）、
実際の対象日範囲、要求期間、サイズ、SHA256、成功/失敗、形式検査結果を記録します。
公表日時と取得日時は同じものとして扱いません。CSRF・セッショントークンはmanifestに入れません。

同じ取得条件で保存済みのファイルがあればSHA256を確認してスキップします。
内容が変わっていればエラーとして停止し、上書きして修復しません。サンプルと月ファイルは
別ファイルで保持します。再取得版が必要なら別の保存名・取得版で管理する実装を先に追加してください。

CSVのBOMと全バイト列の厳密復号、区切り、ヘッダー位置、列名をファイルごとに確認します。
複数の文字コード候補を区別できない場合は曖昧さを明示します。空応答・HTMLや不正形式は
rawとして保存しません。未確認の符号化方式は推測で置換しません。
ZIPのCSVメンバーはメモリ内で検査し、rawに展開しません。XLSXは読み取り専用でシート名・先頭列、
PDFはページ数を検査します。PDF/XLSXの対象期間は手動確認が必要です。

## 監査の意味

manifestの構造・ファイルキー一意性・SHA256・サイズ・対象日の有無を検証します。
JEPXは元の日付×時刻コードについて1〜48・重複・各日の48コマも検査します。
日付を持たない現在スナップショット、未取得PDFは対象期間未確認として報告します。
未登録rawがあっても変更せず一覧化します。

収録日が365日でも、全ユニット・エリア・コマの値が揃うことを保証しません。
単位換算・long化・join・有効期間展開の検証は該当ETLの実装で行います。
今回の取得結果・欠損・未取得範囲・研究判断は [収集結果](../reports/issue_4_acquisition.md) を参照してください。


## 系統情報サービスの連系線空容量

広域予備率連系線情報とは別ソースです。翌々日・策定・全連系線・両方向だけを取得します。
公開HTTPで通常のCSV保存操作を再現し、手動初週と元バイト列が一致した場合のみ年間取得します。

```sh
uv run python scripts/acquire_capacity.py --phase sample
uv run python scripts/acquire_capacity.py --phase full
uv run python scripts/audit_capacity.py --output reports/capacity_fy2025_audit.json
```

7日区間52件＋最終1日の計53件を、
`data/raw/occto/system_interconnector_capacity/dayahead2/planned/` に保存します。
手動原本と自動取得版は別名で保持します。同じ版・同じ期間の手動ファイルがあれば比較し、
一致しなければ取得を停止します。元列が「更新後」の手動第2週は `dayahead2/updated/` に分け、
策定版の正解データとして使用しません。方向は返却CSVで両方向を検証します。
連系線構成は日ごとに記録し、固定11区間とは仮定しません。潮流実績・作業停止計画は取得しません。

## 承認済みraw整理と手動登録

```sh
# 読み取り事前検査。実行はユーザー承認がある対応表だけ。
uv run python scripts/reorganize_raw.py --inventory reports/raw_inventory_20261014.json
# 2026-10-14に承認済み・実行済み。履歴はraw_relocation_manifest参照。
uv run python scripts/reorganize_raw.py --inventory reports/raw_inventory_20261014.json --execute
uv run python scripts/register_manual_raw.py --inventory reports/raw_inventory_20261014.json
```

移動前のSHA256・衝突を全件検査し、排他的hard link作成後に元名を外して移動します。
元バイト列は変えません。元名・元パス・移動先・日時・SHAを
`metadata/raw_relocation_manifest.jsonl` に記録します。
手動分は `status=imported`、不明なURL・取得日時はnull、登録日時は別欄です。
取得記録の `downloaded` と手動登録を区別します。`.DS_Store`は未登録のまま保持します。
第2週は本文の版確認後に追加移動しています。再実行時は移動履歴をたどり、
更新後の保存先を確認してスキップします。古い対応先へ戻しません。
追加確認の根拠は `metadata/raw_relocation_correction_20261014.json` に保存しています。

最新の作業結果は [整理・空容量年間取得](../reports/issue_4_reorganization_and_capacity_20261014.md) を参照してください。
