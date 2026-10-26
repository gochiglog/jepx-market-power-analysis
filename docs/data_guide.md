# データを見る入口

案内を一本化しました。**[READMEの「何ができているか」](../README.md#何ができているか)**から始めてください。

- 表を開く：[日本語データ閲覧ノートブック05](../notebooks/05_data_browser_ja.ipynb)（34表）
- 原本の種類・取得範囲：[原本取得一覧](../reports/issue_4_closeout.md)
- 表の保存先：[閲覧対象一覧](../metadata/view_table_registry.json)
- 日本語名・英語列名・型・単位・定義：[列対応表](../metadata/view_column_dictionary.csv)
- 取得版・早端／遅端・連系線の読み方：[具体例](data_reading_faq.md)

実データは共有repo直下の `data/` です。GitHubには含みません。原本・基本表・比較診断を区別し、閲覧用の基本表を最終分析baselineと解釈しないでください。
以前の長い成果物ガイドはGit履歴で保持しています。今後はREADMEと台帳を更新し、並行する案内を増やしません。

## 純需要の入力確認（工程17）

[需要・固定供給の会計と制約](../reports/17_net_demand_network/README.md)。新しい診断入力は `data/interim/occto/net_demand_network/20261026_diagnostic_v1/`。需要原値は保持し、Rと純需要は未確定nullです。純需要の完成DBではありません。係数の数値化と実際の需給成立も区別します。既存基本市場表・案A・Issue #40の成果物は変更していません。


## 公開R候補の少数サンプル（工程18）

[実ファイルの点検結果](../reports/18_fixed_output_feasibility/README.md)。取得原本は `data/interim/research_reference/20261026_R_feasibility_v1/`、検査表は `data/interim/occto/fixed_output_feasibility/20261026_samples_v3/`。正式な純需要DBではなく、予測断面・FIT範囲を確認する参照資料です。旧固定版は保持しています。


## 実績によるA/B判断用入力（工程19）

[二案とデータ範囲](../reports/19_actual_net_demand/README.md)。`data/interim/occto/actual_net_demand/20261026_preparation_v7/` に4月12,960行の原値付き候補表があります。最終純需要やRSIではありません。地域全原子力と参照32ラベルの原子力を別列にし、中部/関西のそれぞれの未確定値を保持。REI地域別ファイルの8件は保存名と原ヘッダーの地域が異なり、検算は統合CSVの地域ヘッダーを使用しました。原本は変更していません。

## 4月の全法人計算（工程20）

[実行結果・見方](../reports/20_april_all_supplier_balance/README.md)。`data/interim/occto/april_all_supplier_balance/20261026_B30_april_v1/` の `all_firm_balance_results.parquet` が12,960条件の結果、`B30_primary_input_ledger.parquet` が9地域12,960行の実績入力です。純充電を再現しない限定診断。企業除去・RSIではありません。旧版の中部原子力nullはそのまま残し、新版で公式年度ファイルの4月値を使用しました。
