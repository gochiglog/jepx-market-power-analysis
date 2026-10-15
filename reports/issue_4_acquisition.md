# Issue #4 生データ収集結果

基準期間は2025-04-01〜2026-03-31、取得日は2026-10-08（Asia/Tokyo）です。
83ファイル、196,472,418 bytesを公式サイトから元バイト列のまま新規保存しました。
全ファイルでmanifestのサイズ・SHA256一致を確認しました。既存rawの編集・上書き・削除、
long化、結合、単位変換、30分への補間、分析は実施していません。

## 取得結果

| ソース | 保存ファイル数 | 形式・文字コード・BOM | 対象期間・不足 |
|---|---:|---|---|
| JEPXスポット | 1 | CSV、CP932/Shift_JIS候補、BOMなし | FY2025、365日×48コマ |
| JEPX価格感応度（実数値・差分） | 2 | CSV、CP932/Shift_JIS候補、BOMなし | 各FY2025、365日×48コマ |
| OCCTOユニット別発電実績 | 13 | CSV、UTF-8 BOM付き | 1日サンプル＋12か月。対象日365日 |
| HJKSユニット情報 | 1 | CSV、CP932、BOMなし | 現在取得版。FY2025当時の履歴は未確認 |
| HJKS停止・出力低下 | 1 | CSV、CP932、BOMなし | 全停止履歴の現在取得版。当時の公表版・完全性は未確認 |
| OCCTO需要実績 | 13 | CSV、CP932/Shift_JIS候補、BOMなし、波ダッシュ等のUnicode字形差あり | 対象日365日、**元粒度は1時間帯** |
| OCCTO予備率（翌々日、翌日・当日） | 26 | CSV、UTF-8 BOM付き | 各1日サンプル＋12か月。各対象日365日 |
| OCCTO連系線情報（翌々日、翌日・当日） | 26 | CSV、UTF-8 BOM付き | 各1日サンプル＋12か月。各対象日365日 |
| 監視委員会の規制資料 | 0 | 未取得。実形式・対象期間未確認 | サンプル候補もHTTP 403。FY2025適用資料未収集 |
| 高価格日データシート（検証用） | 0 | 未取得。実形式・対象期間未確認 | サンプル候補もHTTP 403。FY2025公表資料未収集 |

日付収録を確認できた9種類で、FY2025の未収録日は0日です。これは**日付の有無**の監査です。
全エリア・全ユニット・全48値の完全性、値の欠損、`***`等の表記の意味は未検証です。
JEPXの3年度ファイルでは日付×時刻コードの重複・範囲外・48コマ欠落も0件でした。
サンプルと月ファイルは重なりますが、ファイルごとにmanifestで識別し、データを結合していません。

## ファイル形式の注意点

- 取得ファイルはすべてCSVです。区切りはカンマ、Excelシート名は該当しません。
  ZIP・XLSX・PDFの検査機能は人工fixtureでテストし、未取得PDFを検査済みとは扱っていません。
- スポットの日付列は`受渡日`、価格感応度は`年月日`です。価格感応度の元列は
  `売500MW`等の表記を保持しています。
- 予備率・連系線CSVは1行目が更新日時、2行目が列名です。
- 発電実績は`00:30[kWh]`〜`24:00[kWh]`の48列を保持しています。
- HJKSユニット情報はヘッダー12列に対し529行が13列、停止情報はヘッダー15列に対し
  58,852行が16列でした。末尾の追加空列を除去せず保存しています。
- HTTP MIMEはJEPXの`application/octet-stream`、HJKSの`text/csv;charset=MS932`、
  OCCTO発電実績・予備率・連系線の`application/csv`等をmanifestに記録しています。
  需要実績の応答は`application/octet?stream;charset=UTF-8`という宣言でしたが、
  実バイト列はUTF-8ではありません。HTTP宣言だけで文字コードを決めていません。
- CP932とShift_JISをバイト列だけでは一意に区別できない場合は候補を併記します。
  需要実績では既知のUnicode字形差を記録し、検査用復号はCP932と明示しています。
  元ファイルは復号・再エンコードしていません。
- Downloadsフォルダは権限を上げてもmacOS側で読み取りを拒否されました。
  手元のUTF-8 BOMサンプルは未検査です。今回の公式取得結果を他の版へ一般化しません。

## 未取得・研究判断

1. **需要実績の1時間粒度**：30分パネルへの扱いを研究側で決める必要があります。
   分割・補間・他ソースへの置換はしていません。元単位も列名にないため追加確認が必要です。
2. **HJKSの履歴**：現在取得版をFY2025当時に観測可能だった情報と同一視しません。
   ユニットの有効期間や停止・復旧見通しの改訂履歴の完全性は未確認です。
3. **翌日・当日の版**：取得できた公表ファイルを保持していますが、翌日初回版から
   当日全更新版までの履歴が揃ったという意味ではありません。
4. **監視委員会資料のHTTP 403**：公式一覧・サンプルPDFとも取得できていません。
   規制資料候補はFY2025適用根拠として採用しておらず、企業分類・有効期間は未確定です。
5. **高価格日資料**：日次全期間を公表するソースではありません。未取得の公表日一覧と
   非公表日を区別できていないため、非公表日を欠損日として数えません。

分析期間・代替ソース・headroom等の研究定義は変更していません。現在の資料では共通の
30分分析パネルの利用可能期間を確定できません。上記の取得不能・判断待ちが残るため、
Issue #4は自動closeせず、PRでは基盤実装と取得済み範囲をレビュー対象にします。

詳細は `metadata/acquisition_manifest.jsonl`、`metadata/data_sources.yml`、
`reports/raw_acquisition_summary.json` を参照してください。manifest・schema・軽量QAだけをGitに入れ、
`data/raw/`内の83ファイルはGit対象外です。

## 出典

- [JEPXスポット公式画面](https://www.jepx.jp/electricpower/market-data/spot/)
- [JEPX価格感応度公式画面](https://www.jepx.jp/electricpower/market-data/spot/virtualprice.html)
- [OCCTOユニット別発電実績マニュアル](https://www.occto.or.jp/assets/occtosystem2/files/20240326_yhks.pdf)
- [OCCTO広域予備率マニュアル](https://www.occto.or.jp/assets/various/occtosystem/manual/20260313_koikiyobiritsuwebkohyo.pdf)
- [OCCTO系統情報サービス](https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/LOGIN_login)
- [HJKSユニット情報](https://hjks.jepx.or.jp/hjks/unit)
- [HJKS停止情報](https://hjks.jepx.or.jp/hjks/outages)
- [監視委員会の高価格日資料一覧](https://www.egc.meti.go.jp/info/business/spike/index.html)
