# 03　価格と価格感応度を整える

[報告書の入口へ戻る](README.md)

実績市場価格と追加注文の仮想価格を整備しました。二つのCSVの基準価格は2,271コマで異なるため、原系列を別々に保持しています。原因解明済みという意味ではありません。

## 最初に読む報告

- [jepx_spot_etl.md](jepx_spot_etl.md)
- [jepx_price_sensitivity_etl.md](jepx_price_sensitivity_etl.md)
- [price_discrepancy_investigation_20261015.md](price_discrepancy_investigation_20261015.md)

## この工程の記録

以下は作業時点の原記録です。計画書・旧判断・診断表も含むため、採用判断は上の報告と[研究判断ログ](../docs/research_decisions.md)を確認してください。

### 説明・計画

- [jepx_price_sensitivity_etl.md](jepx_price_sensitivity_etl.md)
- [jepx_spot_etl.md](jepx_spot_etl.md)
- [price_discrepancy_investigation_20261015.md](price_discrepancy_investigation_20261015.md)
- [price_sensitivity_research_review.md](price_sensitivity_research_review.md)

### 数値表・QA・監査

- [jepx_spot_FY2025_qa.json](jepx_spot_FY2025_qa.json)
- [price_discrepancy_by_period.csv](price_discrepancy_by_period.csv)
- [price_discrepancy_current_web_evidence.json](price_discrepancy_current_web_evidence.json)
- [price_discrepancy_current_web_spotchecks.csv](price_discrepancy_current_web_spotchecks.csv)
- [price_discrepancy_daily.csv](price_discrepancy_daily.csv)
- [price_discrepancy_day_boundaries.csv](price_discrepancy_day_boundaries.csv)
- [price_discrepancy_details.csv](price_discrepancy_details.csv)
- [price_discrepancy_monthly.csv](price_discrepancy_monthly.csv)
- [price_discrepancy_offset_tests.csv](price_discrepancy_offset_tests.csv)
- [price_discrepancy_raw_audit.json](price_discrepancy_raw_audit.json)
- [price_discrepancy_rounding_tax_unit_tests.csv](price_discrepancy_rounding_tax_unit_tests.csv)
- [price_discrepancy_runs.csv](price_discrepancy_runs.csv)
- [price_discrepancy_summary.json](price_discrepancy_summary.json)
- [price_sensitivity_FY2025_baseline_qa.csv](price_sensitivity_FY2025_baseline_qa.csv)
- [price_sensitivity_FY2025_preflight_qa.json](price_sensitivity_FY2025_preflight_qa.json)
- [price_sensitivity_FY2025_qa.json](price_sensitivity_FY2025_qa.json)
- [price_sensitivity_FY2025_raw_hash_audit.json](price_sensitivity_FY2025_raw_hash_audit.json)
- [price_sensitivity_FY2025_spot_mismatches.csv](price_sensitivity_FY2025_spot_mismatches.csv)

