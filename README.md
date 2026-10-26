# 電力市場研究：まずここから

研究目的は **「特定事業者の供給を他社・域外供給で代替できるかという市場構造の評価」** です（RD-021）。
市場支配力の行使、売り渋りの実在、利益増加の認定は今回の目的に含めません。
対象はFY2025（2025年4月1日〜2026年3月31日）。公開データの収集・接続と北海道の記述用散布図まで実施済みです。

**現在の結果は [純需要・固定供給の会計と9地域制約](reports/17_net_demand_network/README.md)です。** 翌々日供給力にPV/風力を計上するルールと、手元でRの内訳を取り出せるかを分離しました。切替前16,608コマの制約を数値化しましたが、R・供給会計未確定のため実ネットワーク需給成立は未判定です。[Issue #40の前検査](reports/16_saved_plan_supply/README.md)も固定保持。RSI・地域順位・最終baselineは未算定です。

## 入口は2つ

- **全体の仕事・進捗を見る：[総合Issue #29](https://github.com/gochiglog/jepx-market-power-analysis/issues/29)**。分野別PJと、その下の実施Issueへ進めます。
- **手元のデータを見る：[日本語データ閲覧ノートブック05](notebooks/05_data_browser_ja.ipynb)**。34表を「基本台帳」と「比較・診断」に分け、列名・単位は日本語で表示します。

ノートブックは上から実行し、「種類」「表」を選んで「表示する」を押してください。「列名・型・定義を開く」で日本語名／英語標準名／単位／型／元列／定義を確認できます。
既存の01・02にある手元の変更は保持しました。今回の画面は**新しい05**です。

## 何ができているか

原本を収集 → 日付・30分コマを統一 → 価格・需要・需給計画・連系線を整備 → 市場表へ接続、という順で進みました。
発電実績と設備台帳の対応には別途診断を行い、参照容量差と公表停止記録を並べています。これは停止補正済み余力ではありません。

| 区分 | 見られる内容 | 読むときの注意 |
|---|---|---|
| 公式原本 | JEPX、OCCTO、HJKS、REI、根拠資料 | [原本取得一覧](reports/issue_4_closeout.md)。加工せず `data/raw/` に保存 |
| 基本台帳（閲覧16表） | 市場基本表、需要3表、計画需給2表、連系線3表、JEPX2表、発電実績、HJKS4版 | 元頻度・出典を保つ。「基本」は最終研究baselineの意味ではない |
| 比較・診断（閲覧18表） | 公表停止記録付き市場4条件、参照容量差、1時間比較、法人記載の参照、北海道の図用表など | 仮定・取得版・対象設備の範囲を区別。別条件を足し合わせない |
| 研究方針の材料 | 問いとデータの対応、3論文、比較設計 | [研究課題と識別可能性](reports/12_research_identification/README.md) |
| 最初の図 | 北海道の30分散布図2種類 | [図と説明](reports/13_hokkaido_scatter/README.md)。回帰・因果推定ではない |

普段見る実績システムプライスはspot市場価格です。感応度の計算基準・原列・監査情報は詳細に保存しています。
「案A」「7,828／58,852」「早端／遅端」「連系線の1行」が分からないときは、[具体例で読むデータの説明](docs/data_reading_faq.md)だけ読めば確認できます。

## 5つのPJと作業場所

| PJ | 子Issue | 実施Issue・作業ブランチ |
|---|---|---|
| 先行研究 | [#30](https://github.com/gochiglog/jepx-market-power-analysis/issues/30) | 読書整理 #36（未着手。実装ブランチ未作成） |
| 研究戦略・制度 | [#31](https://github.com/gochiglog/jepx-market-power-analysis/issues/31) | #3、#23、#25、RSI設計 #38／各 `issue/feature-番号` |
| データ取得・ETL・DB | [#32](https://github.com/gochiglog/jepx-market-power-analysis/issues/32) | #2、#4、#8、#10、#13、#15、#17、#19、保存計画診断 #40、純需要/制約 #42／各 `issue/feature-番号` |
| 分析・アウトプット | [#33](https://github.com/gochiglog/jepx-market-power-analysis/issues/33) | 北海道記述図 #27／`issue/feature-27` |
| 運営・整理 | [#34](https://github.com/gochiglog/jepx-market-power-analysis/issues/34) | 初期構築 #1、案内 #21、今回の再構築 #35／`issue/feature-35` |

今回の純需要/制約ブランチは `issue/feature-42`、依存元は `issue/feature-40`（PR #41）です。PJの階層はIssueで管理し、Gitでは親ブランチをPRのbaseとして表します。
既存Issue・PRは履歴と依存を保持します。**データがローカルで完成していることと、mainへマージ済みであることは別**です。
未統合PRは #16→#18→#20→#22→#24→#26→#28、事務整理はそのheadを継ぐ親ブランチをbaseにした #37。今回もmainへ直接commit・pushせず、PRをマージしません。

## ファイルはどこにあるか

```text
README.md                  全体の入口（このページ）
notebooks/05_*.ipynb        全34表の日本語閲覧
metadata/view_*.{json,csv}  表の所在・英語列名／日本語名／型／単位／定義
metadata/                  原本出典・SHA・対応根拠・出力manifest
reports/                   工程ごとの結果。古い報告は履歴として保持
docs/                      仕様、データの読み方、研究判断ログ
src/, scripts/, tests/     処理・実行・自動検査
data/raw/                  公式原本（変更禁止）
data/interim/              設備実績・診断の固定版
data/processed/            価格・市場・需要・需給・連系線・比較表
.worktrees/                過去のIssue作業フォルダ。成果物の入口ではない
```

実データは**共有repo直下の `data/`** にあり、GitHubには含めません。`.worktrees/`内を探す必要はありません。
市場の閲覧用基本表は次のファイルです（157,680行＝9エリア×365日×48コマ）。

```text
data/processed/occto/demand_supply_direction/20261016_native_v1/market_base_FY2025_with_reserve.parquet
```

Parquetは型を持つ表ファイルです。英語列名の新しい閲覧層を用意し、画面では日本語にしています。固定済みParquetの列名や値を一括変更したわけではありません。

## 詳細が必要なときだけ

[工程順の報告一覧](reports/README.md)／[研究判断ログ](docs/research_decisions.md)／[全34表の所在](metadata/view_table_registry.json)／[全列の対応表](metadata/view_column_dictionary.csv)／[今回の整理結果](reports/14_project_reorganization/README.md)。
古い報告・重複原本・未マージworktreeは勝手に削除しません。削除候補は一覧・SHA・承認と処理結果を残します。研究定義やデータ採用に判断が必要なときは、選択肢・影響を提示してユーザーに確認します。
