# FY2025 電力市場データ基盤

JEPX・OCCTO・HJKSなどの公開データから、地域の需給・連系線と市場価格を同じ日時で調べられるようにする修士研究のプロジェクトです。
対象は2025年4月1日〜2026年3月31日。時刻は日本時間、30分の表は1日48コマです。

**現在は、整備済みデータを研究の問いと結び、何を識別できるか整理する段階です。**
中心の問いは「域内外の代替供給制約は、主要事業者の数量抑制が価格・利益に与える影響をどう変え、他社反応の推測モデルへどう接続できるか」です。
市場環境の記述、数量抑制の採算、実際の行使の識別を分けています。競争的供給者全体の余力、最終headroom、法人帰属・dominant/fringe分類、価格への因果効果は未確定です。

**最初の記述図として、[北海道の散布図2種類](reports/13_hokkaido_scatter/README.md)を作成しました。**
対象は台帳記載法人で確認した3公開設備。17,520コマすべてを残し、翌々日予測需要で分けて表示しています。

## 最初に見る場所

| 知りたいこと | 入口 |
|---|---|
| 何を作り、どこまで進んだか | [現在地と作業の流れ](docs/project_status.md) |
| 実際のデータをどこで見られるか | [成果物ガイド](docs/data_guide.md) |
| 表としてデータを開きたい | [閲覧用ノートブック](notebooks/02_all_tables_browser.ipynb) |
| 報告書を工程順に読みたい | [報告書の目次](reports/README.md) |
| 変数の意味・単位を調べたい | [変数辞書](metadata/variable_dictionary.csv) |
| 研究方針を考える比較材料を見たい | [判断準備の短い資料](reports/11_decision_preparation/README.md)・[比較用7表](notebooks/03_decision_comparison.ipynb) |
| 研究の問いと、公開データで分かる範囲を知りたい | [研究の問いと識別可能性](reports/12_research_identification/README.md) |
| 北海道の第一版の図を見たい | [図と結果](reports/13_hokkaido_scatter/README.md)・[ノートブック04](notebooks/04_hokkaido_scatter.ipynb) |
| 研究上の採用判断を確認したい | [研究判断ログ](docs/research_decisions.md) |

主な閲覧入口は、ローカルの次のファイルです。**157,680行＝9エリア×365日×48コマ**で、価格と30分の需給計画を見られます。

```text
data/processed/occto/demand_supply_direction/20261016_native_v1/
  market_base_FY2025_with_reserve.parquet
```

Parquetは型を保った表形式のファイルです。上のノートブックで表示できます。
閲覧入口の指定は、最終分析baselineの採用判断ではありません。
実データはGit管理外のため、GitHubからcloneしただけでは含まれません。現在の共有作業フォルダに保存しています。

## ディレクトリの役割

```text
04_research_second/
├── README.md           最初の案内
├── docs/               現在地、使い方、仕様、研究判断
├── reports/            工程別の説明と、CSV・JSONの検証記録
├── metadata/           出典、変数辞書、対応表、出力manifest
├── notebooks/          保存済みデータを読む入口
├── src/                正規化・ETL・結合・検証の処理
├── scripts/            処理を実行するコマンド
├── tests/              自動テスト
├── issues/             当初のIssue仕様
├── data/               実データ（Git管理外）
│   ├── raw/            公式原本。内容変更・上書き禁止
│   ├── interim/        設備単位の中間表、固定した診断版
│   └── processed/      JEPX価格、市場表、需要・需給・連系線
└── .worktrees/         別Issueの作業フォルダ（過去の作業も保持）
```

`metadata`のmanifestは、どの原本・版を使ったかとSHA256（内容の指紋）を記録する台帳です。
データを探すときは、まず共有repo直下の`data/`を見てください。`.worktrees/`は実装作業の場所です。

## 作業ブランチと承認の状況

現在の作業はIssue #27の北海道記述図、ブランチ`issue/feature-27`です。Issue #25の研究識別整理と、Issue #23の比較データを継承しています。
ここまでの作業はIssueごとの別フォルダでcommit/pushしていたため、元のフォルダはmainのままでした。
元のフォルダも今回の作業ブランチへ切り替えました。mainに直接commitしません。

設備診断→市場接続→需要・需給の変更は、PR #16→#18→#20の順に依存しています。
**ローカルで成果物ができていることと、mainへマージ済みであることは別です。**
現在の整理も専用PRで扱い、承認なしにマージしません。[ブランチと現在地の詳細](docs/project_status.md#ブランチと成果物の違い)

## 環境と検証

Python 3.12とuvを使います。リポジトリ直下で実行してください。

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run jupyter lab notebooks/02_all_tables_browser.ipynb
```

実rawを使う検査にはローカルのデータが必要です。閲覧だけで再取得やETLの再実行は必要ありません。
研究目的と将来計画は[全体計画](00_MASTER_PLAN.md)、[ロードマップ](01_IMPLEMENTATION_ROADMAP.md)、
作業ルールは[AGENTS.md](AGENTS.md)を参照してください。
ロードマップの工程番号とGitHub Issue番号は異なります。[対応表](metadata/github_issue_map.yml)で確認できます。
