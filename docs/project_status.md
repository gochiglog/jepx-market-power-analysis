# 現在地

全体の進捗・作業分担を **[総合Issue #29](https://github.com/gochiglog/jepx-market-power-analysis/issues/29)** と [README](../README.md)へ集約しました。

原本取得・時間正規化・価格ETL・設備診断・市場接続・需要需給ETL・方向整理・研究判断材料・北海道記述図まで実施済みです。
現在の目的は「特定事業者を他社・域外供給で代替できるか」の構造評価です（RD-021）。
[工程24：北海道集合差・送電の意味・価格接続](../reports/24_april_price_connection/README.md)を完了。旧モデル数値を変更せず、全12,960価格観測・440,640条件枠を接続。図4枚と[実行済みノートブック08](../notebooks/08_april_price_connection.ipynb)へ整理しました。入力の追加仮定と図A/Bの母集団差を明記し、因果・最終baselineは未採用です。

今回の実施Issueは #42（データPJ #32）、branch `issue/feature-42`、依存元 `issue/feature-40`（PR #41）。
元repoのブランチを切り替えて作業し、01・02の未commitのノートブック変更は保持しています。
区切りごとにpytest／ruff後に専用ブランチへcommit／pushし、無承認のmergeはしません。

## ブランチと成果物の違い

データは共有repoの `data/` に保存され、Gitのブランチを切り替えても自動で消えたり戻ったりしません。
実装・文書のmain統合状況とは別に、固定manifestでどのデータ版かを確認します。未統合PRの依存関係はREADMEと総合Issueに記録します。
以前の詳細進捗はGit履歴・工程別報告に保持しています。今後はこのページに同じ詳細一覧を重ねません。
