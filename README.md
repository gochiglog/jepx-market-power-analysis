# JEPX市場支配力研究

公開データから市場・資産・企業の30分コマパネルを構築する修士研究用リポジトリです。
研究仕様は [全体計画](00_MASTER_PLAN.md)、作業順は
[実装ロードマップ](01_IMPLEMENTATION_ROADMAP.md)、作業ルールは [AGENTS.md](AGENTS.md)
を参照してください。

## 環境構築

Python 3.12とuvを使用します。リポジトリ直下で実行してください。

```sh
uv sync --locked
uv run pytest
uv run ruff check .
```

初回はuvが必要なPythonと依存パッケージを取得します。
依存関係を変更する際は `uv lock` で `uv.lock` を更新してください。

## ディレクトリ

- `src/`: 今後実装する取得・正規化・集約処理
- `tests/`: 自動テスト（人工fixtureはここに配置）
- `metadata/`: 出典・スキーマ・変数辞書・対応表
- `scripts/`: 実行スクリプト
- `notebooks/`: 探索分析
- `reports/`: 軽量QA結果・研究判断が必要な点の記録
- `issues/`: 初期Issue仕様
- `data/raw/`: 元データの保存先。変更・上書き禁止
- `data/interim/`, `data/processed/`: 中間・加工データの保存先

`data/raw/` に公式ファイルを元形式のまま新規保存します。既存rawは上書きしません。raw/interim/processedのデータは
Git管理対象外です。

## 開発順序

1. [#1 環境構築](https://github.com/gochiglog/jepx-market-power-analysis/issues/1)
2. [#2 データカタログ・スキーマ](https://github.com/gochiglog/jepx-market-power-analysis/issues/2)
3. #2完了後、[#3 企業対応表](https://github.com/gochiglog/jepx-market-power-analysis/issues/3) と
   [#4 生データ収集](https://github.com/gochiglog/jepx-market-power-analysis/issues/4)

原則1 Issueにつき `issue/feature-<Issue番号>` ブランチを使用します。
commit/pushは明示的な指示がある場合のみ行います。

## 検証範囲

Issue #4の[生データ収集手順](docs/raw_acquisition.md)と
[取得結果・未取得範囲](reports/issue_4_acquisition.md)を追加しました。
元データは `data/raw/` に保存し、Gitへcommitしません。
需要実績は元粒度が1時間で、30分パネルへの扱いは研究判断が必要です。

現在はPython・依存パッケージ・データのGit除外、および
[データカタログ・共通仕様](metadata/README.md)の構造・参照・単位の明示を検証します。
未確認項目は `uv run python scripts/validate_metadata.py` で一覧表示します。
スキーマ、48コマ、キー一意性、単位変換、join統計、有効期間join、日付境界の
テストは対応するスキーマ・処理の実装時に追加します。
基準期間はFY2025、タイムゾーンは `Asia/Tokyo` です。
