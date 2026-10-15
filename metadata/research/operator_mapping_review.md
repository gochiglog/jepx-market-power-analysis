# Issue #3 法人対応の調査結果（最終flag付与前）

## 規制資料の確認

FY2025の対象候補は10法人・11法人×エリアです。適用期間候補は2024-11-01〜2026-03-31です。
[第19回制度設計・監視専門会合資料8](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/019_08_00.pdf)
（2026-03-30）p6の「現在の対象事業者」列で期間を確認し、
[第2回資料4](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/002_04_00.pdf)
（2024-10-15）p9と第8回資料8 p26で正式社名を照合しました。
通常HTTPで原PDFを取得できないため、確認レベルは全行 `official_search_verified` です。
原PDFの脚注・議事録の採択経緯は未確認であり、`official_pdf_verified` の行はありません。
2026年3月の資料による過去期間確認と、FY途中に公表済みだった情報を区別します。

制度上の判定は旧本則の市場区分・容量シェア・PSIと経過措置に基づきます。
長期かつ固定的な相対契約により確保した容量も対象に含まれます。
ここでは委員会の対象一覧を参照し、HJKS設備容量から対象を再判定しません。
需給調整市場の保存PDFはスポットbaselineへ採用しません。
2026-04-01以降に加わる中部電力株式会社をFY2025へ遡及しません。

## 社名・法人・グループの区別

`operator_raw` はHJKSの「発電事業者」欄をそのまま保持します。
`generation_company` は同欄の会社名候補（略記のみ展開）で、法人番号の確認完了を意味しません。
`corporate_group` とその根拠・資料時点は別欄へ記録します。
`regulatory_entity` は対象エリアでの名前一致候補だけを記録し、別法人への推測対応は空欄です。
比較を要する別法人は `regulatory_entity_comparison_candidate` に残します。

| HJKS表記 / 重点法人 | 確認結果 | 今回の扱い |
|---|---|---|
| 東京電力エナジーパートナー | 規制候補だがHJKS4ファイルの事業者欄に同名なし | 対象側の未一致 |
| 東京電力HD・FP・リニューアブルパワー | EPと別法人。公式子会社一覧に役割と法人を区別して掲載 | グループ関係の確認と規制帰属の未確認を分離、manual_review |
| 株式会社JERA | 東京・中部で同名、東北にも表記あり | 東京・中部はexact、東北はエリア不一致manual_review |
| 中部電力ミライズ | 規制候補だがHJKSに同名なし | 対象側の未一致 |
| 中部電力(株) | ミライズと別法人。2020年分社化で販売事業を分離 | manual_review、ミライズへの確定対応なし |
| 四国電力(株) | 正式名の株式会社略記だけを展開 | normalized_exact。規制帰属は承認待ち |
| 関西電力株式会社の中部・北陸行 | 法人名は対象一覧にあるが当該エリアとの組は掲載なし | manual_review。関西エリアの対象を横展開しない |
| その他子会社・運営会社 | 出典による規制帰属を確認できていない | unresolved、グループ名からの自動推測なし |

法人関係の根拠：
[東京電力公式子会社一覧（2023年度末）](https://www.tepco.co.jp/corporateinfo/illustrated/related/consolidated-j.html)、
[中部電力の分社化後の事業体制（2019-10-01）](https://www.chuden.co.jp/publicity/press/3271875_21432.html)、
[JERAの既存火力事業承継（2019-03-01）](https://www.jera.co.jp/news/notice/20190301_79)。
これらは法人・事業の区別を裏付けますが、個別ユニットのFY2025の供給契約や規制対象への帰属を証明しません。

`exact / normalized_exact` は**名前とエリアの一致レベル**です。法人確認・制度適用・資産の帰属が完了したという意味ではありません。
グループ関係の `manual_verified` は別欄で管理し、規制対応のconfidenceに転用しません。
5種類のconfidenceは今回のユーザー指示に従います。

## 対応率と未一致件数

HJKSユニット・停止の4ファイルの和集合を棚卸ししました。84表記・105事業者名×エリアです。
発電実績とのjoinは行っていません。

- 規制候補側：名前・エリア一致9/11組＝81.82%。未一致2組は東京EP・中部ミライズ。
- HJKS側：名前・エリア一致9/105組＝8.57%。未一致96組。
- 内訳：exact 8、normalized_exact 1、manual_review 7、unresolved 89。
- 法人単位では10対象候補のうち8法人に名前候補があります。JERAの2エリアは別組として集計します。

この率は**名前候補の網羅率**であり、資産・発電量のカバー率、join率、dominant確定率ではありません。
全件に `regulatory_eligibility_status=pending_research_approval` を保持し、dominant_flag/fringe_flagは作っていません。
未掲載や未一致だけでfringeへ分類しません。

## 未解決一覧・次の確認

`operator_mapping_unresolved.csv` にmanual_review/unresolvedの96組を列挙し、
`regulatory_target_name_coverage.csv` に対象候補側の11組と未一致2組を記録しています。

1. 原PDFの脚注、延長の採択・施行の経緯、FY当時の公開情報。
2. 東京EP・ミライズと、HJKSの発電事業者/資産との供給契約に基づく対応。
3. 別エリアで観測されるJERA・関西電力の扱い（所在地と分析対象市場を混同しない）。
4. HJKSの版・履歴・有効期間、法人番号・名義変更の確認。ロードマップ#8へ接続。
5. OCCTO発電実績に事業者欄がないため、将来のplant/unit対応時にjoin率・未一致を監査する。

最終dominantとfringeの正式付与は研究側の確認後です。

## 今回の承認に基づくPhase A / B分離

Phase Aは `regulatory_targets.csv` に確定しました。regulatory_target_flag=1は制度上の対象であり、資産のdominantを表しません。
10法人・11組、FY2025を含む期間、全行official_search_verified。原PDF未確認という確認水準は維持します。

Phase Bは `asset_regulatory_mapping_candidates.csv` にHJKSユニット2版の1,206元行を保持しました。
観測された資産識別組は677組です。版間の529組を黙って統合せず、出典SHA・元行番号で区別しています。
資産帰属は全1,206行が未確定（manual_review 827、unresolved 379）。operator名の一致confidenceを別欄に保持します。
mappingのvalid_from/valid_toは未確認のため空欄です。稼働日・出力変更の適用開始日を法人対応の有効日として代用しません。
東京HD/FP/RP→EP、中部電力→ミライズのregulatory_entity_candidateは空欄のままです。
名前一致した資産も供給契約と時点が未確認のため、資産mappingをexactへ引き上げていません。

Generation + HJKS ETL（ロードマップ#8、GitHub未採番）との連携が必要です。Issue #3全体はopen、Phase Aのみ完了です。
