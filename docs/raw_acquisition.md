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

新規取得は `data/raw/<source_id>/` に保存します。元バイト列を復号・再エンコードせず、
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
