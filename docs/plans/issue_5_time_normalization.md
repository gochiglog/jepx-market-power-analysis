# Time normalization（ロードマップ#5 / GitHub Issue #8）実装仕様

GitHubではIssueとPRが共通採番で、#5〜#7を既存PRが使用していました。
このタスクの実Issueは [#8](https://github.com/gochiglog/jepx-market-power-analysis/issues/8)、ブランチは `issue/feature-8` です。
ロードマップ#8 Generation + HJKS ETLは別タスクで、まだGitHub Issueを作成していません。

## 共通契約

元行と全セル、元日時列を保持して `delivery_date / period / timestamp_start / timestamp_end / timezone` を追加します。
数値parse、単位変換、long化、join、分析は行いません。受渡日Dとperiod pに対して開始=D 00:00+(p−1)×30分、終了=開始+30分です。
timezoneはAsia/Tokyo、日時文字列には+09:00を明示します。

| 元表現 | delivery_date | period | 開始 | 終了 |
|---|---|---:|---|---|
| D・period 1 | D | 1 | D 00:00 | D 00:30 |
| D・period 48 | D | 48 | D 23:30 | 翌日00:00 |
| D・終端24:00 | D | 48 | D 23:30 | 翌日00:00 |
| D・始端00:00 | D | 1 | D 00:00 | D 00:30 |

始端/終端を契約で明示します。終端00:00、始端24:00、範囲外period、15分ラベル等は拒否します。
元受渡日を保持し、通常parserで雑に置換しません。FY日数は実日付から求め、閏年に対応します。

## ソース別の出力

| variant | 元時刻 | 正規化と元粒度 |
|---|---|---|
| spot | 受渡日・時刻コード | 30分、整数period 1〜48 |
| sensitivity / sensitivity_diff | 年月日・時刻コード | 各30分。実数/差分を別ファイルに保持 |
| generation | 対象日・00:30[kWh]〜24:00[kWh] | 元のunit×日・48数値列を保持。period・開始・終了の48要素JSON配列を追加 |
| reserve_nextday2 / reserve_latest | 対象年月日・時刻 | 終端00:30〜24:00。更新日時の先頭行も保持 |
| reserve_interconnector_nextday2 / reserve_interconnector_latest | 対象年月日・時刻 | 終端。順逆方向の元数値列・区分・連系線名を保持 |
| system_capacity_dayahead2_planned | 年月日・時刻 | 終端。方向、対象断面、策定/更新後、策定日を保持 |
| manual_rei_validation_power_data | 日付・時刻 | 始端00:00〜23:30。2段ヘッダー・重複列名・統合CSVを保持 |
| demand | 年月日・時間帯 | 1時間は開始/終了のみ、period空欄。日集計はperiod/開始/終了空欄とdaily_summaryで区別 |

発電実績の配列は元の48列と順序対応します。QAのwide_time_columnsに元列一覧を保存し、time_granularity=wide_30minを明示します。
値のlong化はロードマップ#8で行います。REIは追加列の上段をtime_normalizationとし、元212列の2段ヘッダーを変更しません。
全ソースへsource_record_id=入力SHA256、source_row_number=CSV論理行番号を追加します。引用セル内改行を含む物理行番号とは異なります。

## 時刻参照の根拠

JEPXは元ファイルの1〜48時刻コードを共通契約へ対応付けます。
OCCTOは検査した全ファイルの00:30〜24:00の48ラベルを終端として対応付けます。
[広域予備率公式操作マニュアル](https://www.occto.or.jp/assets/occtosystem2/files/20250306_koikiyobiritsuwebkohyo.pdf)のCSV項目表（p15/16/24/25）と原ファイルの時刻列を照合しました。
表は時刻形式hh:mmを記載しています。終端対応は原ラベル範囲と1日48コマの契約に基づき明示し、更新日時を受渡時刻へ流用しません。
FY2024の翌々日「最大需要/最小予備率」行はこのadapterの対応範囲外です。

REIは[公式公開JavaScript](https://www.renewable-ei.org/statistics/electricity/js/bundle.js)のCSV生成でepochを日付/時刻へ出力し、2024年度以降の時刻選択に1800秒刻みを用いることと、実CSVの00:00〜23:30を確認しました。
始端として対応し、補助データの位置付けを維持します。数値の平均区間・推定潮流の列は今回解釈を確定しません。

## 入力選択・出力・encoding

manifestから自動年度/月/週ファイルを選び、JEPX年度ファイルとREI統合手動1件を採用します。
空容量第1週は年間取得で再利用されたsampleです。条件と期間を明示して含めます。
手動コピー、1日sample、REI地域別、HJKSは対象外です。対象行のdrop/重複除去はせず、選択範囲の重複はQA失敗にします。

入力をmanifestのSHA256と照合してから読み、処理後も照合します。encodingは毎ファイルの全バイト列から既存検査関数で判定します。
CP932/Shift_JISが曖昧なものは曖昧性と使用したinspection_decoderを記録します。
rawは書き換えず、interimのみUTF-8 BOMで出力します。数値は文字列として元の桁・表記を保持します。
出力先は元rootのdata/interim以下に制限し、rawへの出力と既存出力の上書きを拒否します。
parse不能行は元セルを残してparse_errorとQAに記録し、全体を不合格にします。

受渡日×系列のコマ重複・欠落とFY対象日を検査します。ブロックNoはコマ間で変化するため、ブロック情報の48コマ監査はエリア単位です。
連系線は観測名×方向等の元系列を使い、11本/13本の固定数を仮定しません。
これは観測系列の日内完全性であり、未観測資産や個別数値欠損の保証ではありません。

## 実行・検証・後続

専用worktreeから、実データを持つ元repoを明示します。再実行は新しいrunディレクトリへ出力します。

```sh
../../.venv/bin/python scripts/normalize_time.py \
  --data-root /Users/goto/Documents/Uni/04_research_second \
  --fiscal-year 2025 \
  --report reports/time_normalization_fy2025_audit.json
```

period1/48、00:00/00:30/23:30/24:00、月末/年末/FY境界/2026-03-31、閏日、48コマ・重複、timezone、raw不変、可変連系線数、全adapterの元セル保持をテストします。
研究rawがある環境ではJEPXの17,520件を実ファイルで検証し、ないCIでは実データ部分のみskipします。
HJKS台帳は48コマ化しません。outage interval joinはロードマップ#8、需要の30分利用方法は#9、REI推定潮流/区間意味は#10へ移管します。
企業対応・headroom・panel・分析に着手せず、ユーザー確認までcommit/pushしません。


## interimの命名

予備率4種類は取得種別コードを使わず、
`occto_reserve_{block|interconnector}_{dayahead2|dayahead_actual}_{開始日}_{終了日}.csv` とします。
開始日/終了日はmanifestの記録を使います。元rawの取得コード・元名は出典として保持します。
