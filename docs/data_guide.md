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

## 固定供給を残す企業除去試行（工程21）

[結果と保留の読み方](../reports/21_adjustable_removal/README.md)。`data/interim/occto/adjustable_removal/20261027_adjustable_removal_v3/` の `all_and_removed_net_multipliers.parquet` に全440,640枠の数値/保留があります。`regional_trial_uppers.parquet` は調整済み除去量、`asset_attribution_proxy.parquet` は全491設備の名参照代理です。正式RSIや所有/支配/契約の確定値ではなく、計算不能を0にしていません。旧市場・案A・工程20を保持しています。


## 4月試行の比較・図（工程22）

[図と短い説明](../reports/22_april_trial_comparison/README.md)、[実行済みノートブック06](../notebooks/06_april_trial_comparison.ipynb)。比較データは `data/interim/occto/april_trial_comparison/20261027_comparison_v1/`。工程21固定版は保持し、全時刻の状態・停止条件の対比較・企業除去後の9地域同時成立を別表にしています。新しい全国RSIや最終baselineではありません。


## 17条件内の一致・送電包絡解除（工程23）

[図と説明](../reports/23_april_constraint_diagnosis/README.md)、[ノートブック07](../notebooks/07_april_constraint_diagnosis.ipynb)。新しい比較版は `data/interim/occto/april_constraint_diagnosis/20261027_constraints_v2/`。全時刻の一致区分、包絡解除比較14条件の同時成立/倍率、重点4法人の地域別供給範囲と診断専用過不足を保存します。14は送電解除の比較条件数で、新しい停止条件ではありません。旧固定版と17供給条件は不変です。


## 4月価格との記述的接続（工程24）

[入力と図の説明](../reports/24_april_price_connection/README.md)、[ノートブック08](../notebooks/08_april_price_connection.ipynb)。実データは `data/interim/occto/april_price_connection/20261027_price_v3/`。12,960価格観測と、同じ価格を34条件で反復する440,640枠を分けています。図Bは純需要非正も含む全時刻であり、図Aの有効倍率時刻とは母集団が異なります。旧固定版は保持し、因果推論・最終baselineではありません。
