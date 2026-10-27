# 報告書はここから読む

**全体の入口は [README](../README.md) と [総合Issue #29](https://github.com/gochiglog/jepx-market-power-analysis/issues/29) です。ここは工程別の詳細・履歴を調べる目次です。**
報告書を全部読む必要はありません。下の01→08が、原本収集から現在の市場表までの流れです。
各工程の案内には、短い説明、最初に読む報告、数値表・監査へのリンクを分けて置いています。
番号は工程順であり、GitHub Issue番号ではありません。

| 順番 | 工程別の入口 | 既存記録数 |
|---|---|---:|
| 01 | [原本を集めて保管する](01_raw_collection.md) | 18件 |
| 02 | [日付と30分コマをそろえる](02_time_normalization.md) | 3件 |
| 03 | [価格と価格感応度を整える](03_jepx_prices.md) | 22件 |
| 04 | [制度上の対象事業者を確認する](04_regulatory_targets.md) | 1件 |
| 05 | [発電実績と設備台帳を対応させる](05_asset_mapping.md) | 39件 |
| 06 | [参照容量差と公表停止記録を並べる](06_reference_gap_records.md) | 39件 |
| 07 | [地域と30分コマで市場へつなぐ](07_market_connection.md) | 6件 |
| 08 | [需要・需給計画・連系線を追加する](08_demand_supply_network.md) | 4件 |
| 09 | [承認・統合の履歴を確認する](09_git_history.md) | 3件 |

## 続きの工程

- [10　現在地・成果物・報告書の案内](10_project_navigation/README.md)
- [11　研究方針を決めるための比較材料](11_decision_preparation/README.md)（1時間比較・方向別表示・台帳事業者の範囲）
- [12　代替供給制約・数量抑制の採算・行動の識別](12_research_identification/README.md)（研究の問いとデータ、制度定義、3論文、モデル比較の設計）
- [13　北海道の参照容量差・価格・輸入方向空容量の散布図](13_hokkaido_scatter/README.md)（17,520コマ、3設備の参照群、需要4群）

- [14　PJ再構築・日本語閲覧・保存と整理](14_project_reorganization/README.md)

- [15　RSI算定設計：地域・法人別の代替供給と式―データ対応](15_rsi_design/README.md)
- [16　保存翌々日計画：全法人前検査・停止反映・9地域LPの範囲](16_saved_plan_supply/README.md)
- [17　純需要・固定供給の会計と9地域ネットワーク入力](17_net_demand_network/README.md)
- [18　固定供給Rの公開範囲・少数ファイル検証・限定集合の設計](18_fixed_output_feasibility/README.md)
- [19　実績固定出力を使うA/Bの最初の計算仕様](19_actual_net_demand/README.md)

## エクスプローラーで探す

VS Codeでは、`01_raw_collection.md` などを展開すると、その工程の既存報告・CSV・JSONを見られます。
`.vscode/settings.json` のファイル入れ子表示により、135件の旧記録を9つの工程の下へ折り畳みました。
必要ならエクスプローラーの「ファイルの入れ子表示」を無効にして、元の一覧に戻せます。
これは表示上の整理です。固定版のSHA検証とコードの参照を保つため、旧ファイルの実際の場所・名前・内容は変えていません。
GitHubや他のエディタでは、この目次から工程別に読んでください。

## これから増やす報告の置き方

新しい報告は、工程別フォルダの `README.md` を読み物として更新します。
必要な数値表は `qa/`、その時点の計画・長い経緯は `history/` へ置きます。
今回の整理は [10_project_navigation/](10_project_navigation/README.md) にまとめました。
例：`reports/11_<工程名>/README.md`、`qa/20261016_<内容>.json`。
既存スクリプトの出力先は今回変更していません。改修する際は参照・固定manifestを確認し、既存版へ上書きしない形で新出力先を指定します。

## 読む順番のルール

報告本文は「何を知りたかったか → 今回分かったこと → データをどこで見られるか → 限界と次の判断」の順にします。
技術的な件数や全列の検査はQAへ分けます。新しい日付の報告が自動的に研究baselineになることはありません。

- [20：4月の実績純需要・9地域全法人LP](20_april_all_supplier_balance/README.md)：成立/不成立・診断過不足と会計。

- [21：4月の固定Rを残す調整可能供給除去試行](21_adjustable_removal/README.md)：純需要倍率、1未満・保留、同一入力/共通時刻の送電比較。

- [22：4月試行を図で読む・送電/停止比較・9地域同時成立](22_april_trial_comparison/README.md)：全時刻分母、条件付きの倍率、JERAの未算定。

- [23：4月の17条件内で残る特徴と送電・供給範囲の診断](23_april_constraint_diagnosis/README.md)：全条件内の一致、11組包絡解除、停止保留の集合差、代表時刻。

- [24：4月入力2点の確認と価格の記述的接続](24_april_price_connection/README.md)：北海道462/250MWの差、送電の意味、倍率/判定区分と価格。

- [25：北海道2設備・送電留保枠・数式実装の監査](25_central_spec_audit/README.md)：公式根拠による非重複確認、17条件の別版比較、B判定の根拠、実ソルバー行列。

- [26：論文の図表設計と4月試行・FY2025拡張のギャップ](26_paper_figure_plan/README.md)：本文図表6点の仕様、付録、版整合、7系列の未処理/未保有/定義障害。追加計算・取得なし。
