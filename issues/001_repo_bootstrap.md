# Issue #1: Repo bootstrap

## 目的
研究用Pythonリポジトリを再現可能な形で初期化する。

## 作業
- Python 3.12
- uv初期化
- pandas, numpy, pyarrow, openpyxl, scipy, statsmodels, matplotlib, jupyter
- pytest, ruff
- README/AGENTS.md確認
- ディレクトリ作成
- .gitignore設定

## 完了条件
- `uv run pytest` が成功
- `uv run ruff check .` が成功
- rawデータがGit対象外
- 最小テスト1件あり

## Codexへの指示
AGENTS.mdを読み、このIssueの範囲だけ実装する。完了後に変更ファイルとテスト結果を日本語で報告する。
