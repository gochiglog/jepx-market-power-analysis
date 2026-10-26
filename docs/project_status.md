# 現在地

全体の進捗・作業分担を **[総合Issue #29](https://github.com/gochiglog/jepx-market-power-analysis/issues/29)** と [README](../README.md)へ集約しました。

原本取得・時間正規化・価格ETL・設備診断・市場接続・需要需給ETL・方向整理・研究判断材料・北海道記述図まで実施済みです。
現在の目的は「特定事業者を他社・域外供給で代替できるか」の構造評価です（RD-021）。
[工程23：17条件内の一致と送電・供給範囲](../reports/23_april_constraint_diagnosis/README.md)を完了。全8法人の全時刻を保持し、重点4法人に11組の単独解除・既存包含群の解除とL1過不足診断を追加しました。図4枚・[再現ノートブック07](../notebooks/07_april_constraint_diagnosis.ipynb)へ整理。既存17供給条件・固定R・除去代理・最終baselineは変更していません。

今回の実施Issueは #42（データPJ #32）、branch `issue/feature-42`、依存元 `issue/feature-40`（PR #41）。
元repoのブランチを切り替えて作業し、01・02の未commitのノートブック変更は保持しています。
区切りごとにpytest／ruff後に専用ブランチへcommit／pushし、無承認のmergeはしません。

## ブランチと成果物の違い

データは共有repoの `data/` に保存され、Gitのブランチを切り替えても自動で消えたり戻ったりしません。
実装・文書のmain統合状況とは別に、固定manifestでどのデータ版かを確認します。未統合PRの依存関係はREADMEと総合Issueに記録します。
以前の詳細進捗はGit履歴・工程別報告に保持しています。今後はこのページに同じ詳細一覧を重ねません。
