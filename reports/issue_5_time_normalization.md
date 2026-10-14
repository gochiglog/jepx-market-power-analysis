# Time normalization 作業報告（ロードマップ#5 / GitHub #8）

## 結果

指定ソースの時間キーadapterを実装し、FY2025の129ファイル・1,414,286行を処理しました。
結果は元repoの `data/interim/time_normalization/20261014T232707/` に保存しています。
研究データはGit管理外です。出力サイズは約933MiBです（発電実績の48要素時間配列を含む）。

全variantで365日を確認しました。時間キーの重複・不正/欠落コマグループ・parseエラーは0件です。
原ヘッダー・先頭更新情報・全元セル・行順について、実装とは別の読取比較で129ファイル全行の一致を確認しました。
入力129件の処理前後SHA256と、登録済みraw全203件の監査はすべて一致しました。

| 対象 | ファイル数 | 元行数/出力行数 | 時間正規化 |
|---|---:|---:|---|
| JEPX spot | 1 | 17,520 | 365×48 |
| JEPX price sensitivity 実数 | 1 | 17,520 | 365×48 |
| JEPX price sensitivity 差分 | 1 | 17,520 | 365×48 |
| OCCTO unit generation | 12 | 176,428 | 48列保持、48要素の時間配列 |
| OCCTO reserve block 翌々日 | 12 | 175,200 | エリア×日48コマ |
| OCCTO reserve block 翌日・当日 | 12 | 175,200 | エリア×日48コマ |
| OCCTO reserve interconnector 翌々日 | 12 | 209,520 | 観測連系線×日48コマ |
| OCCTO reserve interconnector 翌日・当日 | 12 | 209,472 | 観測連系線×日48コマ |
| OCCTO system interconnector capacity | 53 | 388,896 | 日×観測連系線×方向48コマ |
| REI power-data 統合CSV | 1 | 17,520 | 始端時刻、2段ヘッダー保持 |
| OCCTO demand | 12 | 9,490 | 8,760時間行＋730日集計、period空欄 |

2026-03-14以降の中部フェンス・関西フェンスは元セルを保持しています。
空容量の日別系列数は追加前22、追加後26（元区間×両方向）ですが、コードはこの数を固定していません。
発電実績の48列では8,468,544区間の時刻対応を検査しました。値のlong化/単位変換はしていません。
検証は観測系列の日内完全性です。未観測資産や数値欠損の完全性は後続ETL/QAで確認します。

## 変更ファイル

- `src/time_normalization.py`：日付・コマ・始端/終端・時間粒度契約、全adapter、選択規則、監査。
- `scripts/normalize_time.py`：manifestによる年度入力選択、interim出力、QA生成。
- `tests/test_time_normalization.py`：境界・エラー・各adapter・元セル/原本不変・実JEPXの検証。
- `docs/plans/issue_5_time_normalization.md`：ソースごとの仕様、文字コード、再実行方法、除外範囲。
- `metadata/github_issue_map.yml`：ロードマップ番号とGitHub番号の対応。
- `reports/time_normalization_fy2025_audit.json`：件数・不変性・日別系列数・出典等の軽量QA。

## テスト結果

pytest 114 passed（既存69＋時間正規化45）、ruff通過。
period1/48、00:00/00:30/23:30/24:00、月末/年末/FY境界、2026-03-31、閏年、48コマ、
duplicate key、timezone、raw不変、可変連系線数、需要元粒度、全adapterの元セル保持を確認しました。
JEPXの17,520件は実rawを使用するテストで確認しています。

## 未解決と次のタスク

法人対応・HJKS版/履歴・需要の30分利用方法・REI推定潮流対象列は解決扱いにしません。
CSV文字コードのCP932/Shift_JIS判別不能性は検査記録に残しています。raw原バイトは保持しています。
今後のETLは時間キーと元列を引き継ぎ、ソース固有のキー・版・粒度を確認してください。

次はロードマップ#6 JEPX spot ETLを推奨します。並行してIssue #3の法人/契約manual_reviewを進めます。
generationとHJKSのjoin、headroom、dominant/fringe集約、需要補間、congestion、panel、分析には着手していません。

専用worktree `.worktrees/issue-8` / branch `issue/feature-8` に保存し、2026-10-14にユーザー承認済み。再検証後にこのブランチをcommit/pushします。


## interimファイル名の改善（ユーザー指摘対応）

取得コードだけでは内容を識別しにくいため、予備率4種類のinterim月ファイル48件を改名しました。
rawの名前・内容は変更していません。改名前後のinterim SHA256も一致しています。

| 従来の先頭コード | 新しい先頭名 | 意味 |
|---|---|---|
| 05_ | occto_reserve_block_dayahead2_ | ブロック・翌々日 |
| 02_ | occto_reserve_block_dayahead_actual_ | ブロック・翌日/当日 |
| 06_ | occto_reserve_interconnector_dayahead2_ | 連系線・翌々日 |
| 04_ | occto_reserve_interconnector_dayahead_actual_ | 連系線・翌日/当日 |

例：`occto_reserve_interconnector_dayahead_actual_2025-04-01_2025-04-30.csv`。
今後のadapter出力も同じ命名規則を使います。QAには元rawのパス・公式元名、改名前のinterimパス、SHA256を保持しています。

再確認：保存済み全129ファイルの元セル・追加時間列・SHA256・既存QA一致を `scripts/verify_time_outputs.py` で確認済み。
