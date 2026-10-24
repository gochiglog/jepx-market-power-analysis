# 現在地

全体の進捗・作業分担を **[総合Issue #29](https://github.com/gochiglog/jepx-market-power-analysis/issues/29)** と [README](../README.md)へ集約しました。

原本取得・時間正規化・価格ETL・設備診断・市場接続・需要需給ETL・方向整理・研究判断材料・北海道記述図まで実施済みです。
現在の目的は「特定事業者を他社・域外供給で代替できるか」の構造評価です（RD-021）。
[RSI算定設計](../reports/15_rsi_design/README.md)を整理し、式→データ列の対応と最小試行の追加仮定まで提示しました。RSI値・割合・順位曲線は未算定。研究baseline、最終headroom、法人帰属・dominant/fringeは未確定です。

今回の実施Issueは #38（研究戦略PJ #31）、ブランチは `issue/feature-38`、依存元は `issue/feature-35`（PR #37）。
元repoのブランチを切り替えて作業し、01・02の未commitのノートブック変更は保持しています。
区切りごとにpytest／ruff後に専用ブランチへcommit／pushし、無承認のmergeはしません。

## ブランチと成果物の違い

データは共有repoの `data/` に保存され、Gitのブランチを切り替えても自動で消えたり戻ったりしません。
実装・文書のmain統合状況とは別に、固定manifestでどのデータ版かを確認します。未統合PRの依存関係はREADMEと総合Issueに記録します。
以前の詳細進捗はGit履歴・工程別報告に保持しています。今後はこのページに同じ詳細一覧を重ねません。
