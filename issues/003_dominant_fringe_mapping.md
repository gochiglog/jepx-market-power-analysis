# Issue #3: Dominant / fringe mapping（2段階）

## Phase A：規制対象一覧（ユーザー承認で確定）

`metadata/research/regulatory_targets.csv` に制度上の法人×市場エリア×有効期間のみを保存する。
FY2025対象は10法人・11組、期間2024-11-01〜2026-03-31。regulatory_target_flagは制度対象を表し、資産flagではない。
全行official_search_verified。原PDFがHTTP403のためofficial_pdf_verifiedにしない。
出典、ページ、期間、確認レベルを保持する。需給調整市場資料やFY2026の親会社追加を混ぜない。

## Phase B：資産・運営法人・契約供給力の対応（後続と連携）

`asset_regulatory_mapping_candidates.csv` にHJKS台帳の各版・元行を保持して候補を記録する。
operator_raw / generation_company / corporate_group / regulatory_entity_candidateを分離する。
法人・グループが同じことだけで資産帰属を確定しない。mapping期間が不明なら空欄を維持する。
exact / normalized_exact / manual_verified / manual_review / unresolvedの5区分を使う。
名前一致のconfidenceと資産帰属のmapping_statusは独立に記録する。
東京EP・HD・FP・RP、JERA、中部ミライズ・中部電力は別法人として扱う。

Generation + HJKS ETL（ロードマップ#8、GitHubは未採番）で版・履歴・plant/unit対応を確認し、Phase Bを進める。
GitHub #8は時間正規化であり、Generation + HJKS ETLとは別Issueである。

## 完了条件

- [x] Phase A：出典・確認レベル付きの規制対象法人×エリア×期間表
- [x] Phase B入口：候補表・未解決件数・元版/元行の識別
- [ ] Phase B完了：個別資産・契約供給力・有効期間の根拠確認
- [ ] 将来join時のmatch率・未一致件数・interval/date boundaryテスト
- [ ] 研究確認後の正式資産flag

Issue全体はPhase Bまで完了するまでopenとし、現段階ではdominant_asset_flag / fringe_flagを付与しない。
容量閾値や未掲載だけで分類しない。
