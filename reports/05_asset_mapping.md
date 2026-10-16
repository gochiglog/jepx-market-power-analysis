# 05　発電実績と設備台帳を対応させる

[報告書の入口へ戻る](README.md)

OCCTOとHJKSのコード・設備粒度の違いを診断しました。未対応発電量は、発電実績の欠落量ではありません。ユニットを保持し、必要に応じて発電所へ集約できる設計です。

## 最初に読む報告

- [generation_hjks_diagnostics.md](generation_hjks_diagnostics.md)
- [unmatched_generation_diagnosis_20261016.md](unmatched_generation_diagnosis_20261016.md)
- [top10_official_asset_review_20261016.md](top10_official_asset_review_20261016.md)
- [asset_analysis_readiness_20261016.md](asset_analysis_readiness_20261016.md)

## この工程の記録

以下は作業時点の原記録です。計画書・旧判断・診断表も含むため、採用判断は上の報告と[研究判断ログ](../docs/research_decisions.md)を確認してください。

問い合わせ案は当時の履歴です。現在は問い合わせ・準備・回答待ちを作業条件にしません。

### 説明・計画

- [asset_analysis_readiness_20261016.md](asset_analysis_readiness_20261016.md)
- [asset_analysis_readiness_plan_20261016.md](asset_analysis_readiness_plan_20261016.md)
- [asset_code_inquiry_draft_20261016.md](asset_code_inquiry_draft_20261016.md)
- [generation_hjks_diagnostics.md](generation_hjks_diagnostics.md)
- [top10_official_asset_review_20261016.md](top10_official_asset_review_20261016.md)
- [top10_official_review_plan_20261016.md](top10_official_review_plan_20261016.md)
- [unmatched_generation_diagnosis_20261016.md](unmatched_generation_diagnosis_20261016.md)

### 数値表・QA・監査

- [additional_mapping_candidates_20261016.csv](additional_mapping_candidates_20261016.csv)
- [asset_analysis_readiness_hash_audit_20261016.json](asset_analysis_readiness_hash_audit_20261016.json)
- [asset_analysis_readiness_qa_20261016.json](asset_analysis_readiness_qa_20261016.json)
- [asset_candidate_analysis_use_20261016.csv](asset_candidate_analysis_use_20261016.csv)
- [asset_capacity_readiness_20261016.csv](asset_capacity_readiness_20261016.csv)
- [asset_mapping_scenarios_20261016.csv](asset_mapping_scenarios_20261016.csv)
- [asset_scenario_coverage_20261016.csv](asset_scenario_coverage_20261016.csv)
- [capacity_mapping_readiness_20261016.csv](capacity_mapping_readiness_20261016.csv)
- [code_structure_census_20261016.csv](code_structure_census_20261016.csv)
- [coverage_scenarios_20261016.csv](coverage_scenarios_20261016.csv)
- [generation_hjks_529_mapping_candidates.csv](generation_hjks_529_mapping_candidates.csv)
- [generation_hjks_677_mapping_candidates.csv](generation_hjks_677_mapping_candidates.csv)
- [generation_hjks_FY2025_qa.json](generation_hjks_FY2025_qa.json)
- [generation_hjks_raw_hash_audit.json](generation_hjks_raw_hash_audit.json)
- [hjks_outage_duplicate_update_candidates.csv](hjks_outage_duplicate_update_candidates.csv)
- [hjks_snapshot_comparison.csv](hjks_snapshot_comparison.csv)
- [plant_code_relations_20261016.csv](plant_code_relations_20261016.csv)
- [plant_unit_coverage_gap_20261016.csv](plant_unit_coverage_gap_20261016.csv)
- [top10_current_hjks_units_20261016.csv](top10_current_hjks_units_20261016.csv)
- [top10_occto_equipment_structure_20261016.csv](top10_occto_equipment_structure_20261016.csv)
- [top10_official_mapping_evidence_20261016.csv](top10_official_mapping_evidence_20261016.csv)
- [top10_official_review_qa_20261016.json](top10_official_review_qa_20261016.json)
- [top10_reference_raw_hash_audit_20261016.json](top10_reference_raw_hash_audit_20261016.json)
- [unmatched_generation_by_area_20261016.csv](unmatched_generation_by_area_20261016.csv)
- [unmatched_generation_by_code_20261016.csv](unmatched_generation_by_code_20261016.csv)
- [unmatched_generation_by_diagnostic_cluster_20261016.csv](unmatched_generation_by_diagnostic_cluster_20261016.csv)
- [unmatched_generation_by_fuel_20261016.csv](unmatched_generation_by_fuel_20261016.csv)
- [unmatched_generation_by_month_20261016.csv](unmatched_generation_by_month_20261016.csv)
- [unmatched_generation_raw_hash_audit_20261016.json](unmatched_generation_raw_hash_audit_20261016.json)
- [unmatched_generation_summary_20261016.json](unmatched_generation_summary_20261016.json)
- [unmatched_plants_20261016.csv](unmatched_plants_20261016.csv)
- [unmatched_units_20261016.csv](unmatched_units_20261016.csv)

