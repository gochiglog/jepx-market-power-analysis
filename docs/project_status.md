# 現在地

全体の進捗・作業分担を **[総合Issue #29](https://github.com/gochiglog/jepx-market-power-analysis/issues/29)** と [README](../README.md)へ集約しました。

原本取得・時間正規化・価格ETL・設備診断・市場接続・需要需給ETL・方向整理・研究判断材料・北海道記述図まで実施済みです。
現在の目的は「特定事業者を他社・域外供給で代替できるか」の構造評価です（RD-021）。
[工程28：年間診断を論文の中心結果へ整理](../reports/28_paper_central_result/README.md)を完了しました。共通16,848コマ・8法人の排他的3区分、北陸拡大/切替前後/人工例の方法図、原文§4–6の1ページ対照、本文/付録/再現資料の配置、同じ3区分の価格候補とD/R/時間/季節構成を保存しました。新規取得・LPなし。JERA767＋14,220＋1,861＝16,848を照合し、旧工程27は不変です。

添付議事録への[研究方針の回答](research_direction_from_minutes.md)を追加しました。新LP・取得・定義変更は行わず、主図の解釈、JERA未算定、実際の代替配分と可能範囲、CVへの接続条件を区別しました。

現在のIssueは #48、branch `issue/feature-48`、[Draft PR #49](https://github.com/gochiglog/jepx-market-power-analysis/pull/49)。依存元 `issue/feature-46`（Draft PR #47）。[閲覧ノートブック10](../notebooks/10_paper_central_result.ipynb)と[一枚要約](../reports/28_paper_central_result/one_page_result.pdf)が入口です。最終baseline・契約帰属・企業不可欠性の認定は行いません。
元repoのブランチを切り替えて作業し、01・02の未commitのノートブック変更は保持しています。
区切りごとにpytest／ruff後に専用ブランチへcommit／pushし、無承認のmergeはしません。

## ブランチと成果物の違い

データは共有repoの `data/` に保存され、Gitのブランチを切り替えても自動で消えたり戻ったりしません。
実装・文書のmain統合状況とは別に、固定manifestでどのデータ版かを確認します。未統合PRの依存関係はREADMEと総合Issueに記録します。
以前の詳細進捗はGit履歴・工程別報告に保持しています。今後はこのページに同じ詳細一覧を重ねません。
