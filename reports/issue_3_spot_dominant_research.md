# Issue #3：スポット市場の規制対象と法人対応候補（調査段階）

## 調査結果

保存済み47ページPDFは需給調整市場の事前的措置の資料です。スポット市場のbaseline対象一覧には使用しません。
今回の対象は、スポット市場で限界費用価格による供出が特に強く求められる規制上の事業者です。
設備容量や企業名の類似から独自に分類していません。**dominant_flag / fringe_flagは付与していません。**

公式配布資料・PDFの検索抽出から、以下を確認しました。PDF本体への通常HTTPは403であり、
全文・ページ画像・脚注を原本で検証できていません。二次サイトの要約は根拠に採用していません。
調査CSVは公式検索抽出に基づく候補です。原本・議事録・法人対応確認まで最終mappingとして使いません。

## 正式資料候補

| 資料 | 公表/配布日 | 市場 | 適用期間/役割 | 確認範囲 |
|---|---|---|---|---|
| 第2回 資料4：スポット市場への限界費用価格での供出が求められる対象事業者の考え方 | 2024-10-15 | スポット | 当初2024-11-01〜2025-10-31 | 表紙・再掲された判定/一覧の公式検索抽出 |
| 第5回 資料9：同題の考え方 | 2025-01-30 | スポット | 本則/経過措置の見直し案。施行済みと扱わない | 公式検索抽出 |
| 第8回 資料8：スポット市場に対して限界費用価格での供出が求められる事業者の考え方 | 2025-04-25 | スポット | p26に当初期間の対象一覧を再掲 | 表紙日付とp26。配布ページの更新日表記の不整合も記録 |
| 第568回 公開3：適正な電力取引についての指針の改定の建議 | 2025-05-12 | スポット | p5に旧対象事業者の条件付き継続案 | 案を実施済みの延長根拠として単独採用しない |
| 第19回 資料8：スポット市場への限界費用価格での供出が特に強く求められる対象事業者について | 2026-03-30 | スポット | p6の「現在の対象事業者」列は2024-11-01〜2026-03-31 | 公式配布ページと公式PDF検索抽出 |

出典：[第2回資料4](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/002_04_00.pdf)、
[第5回資料9](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/005_09_00.pdf)、
[第8回資料8](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/008_08_00.pdf)、
[第568回公開3](https://www.egc.meti.go.jp/activity/emsc/pdf/568_03_00.pdf)、
[第19回資料8](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/019_08_00.pdf)、
[第19回公式配布ページ](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/019_haifu.html)。

公表日は資料表紙/会合配布日として記録しています。実際のWeb公開時刻は未確認です。
後期の確認はFY終了直前の資料による過去期間の確認であり、各時点で観測可能だった公表資料とは区別します。
判定基準は委員会が整理した旧本則・経過措置（市場区分・シェア・PSI等）を資料の根拠として参照します。
ここでraw容量から再判定しません。基準案・建議・施行・対象事業者判定を同一視しません。

## FY2025を含む対象一覧候補

第19回資料8 p6の**旧期間列**と第8回資料8 p26の旧一覧を照合しています。
旧期間は2024-11-01〜2026-03-31で、FY2025全体がその範囲に含まれます。
10事業者・11事業者×エリアの組です。株式会社JERAは東京と中部の2行です。

| 対象エリア | 規制上の事業者名候補 |
|---|---|
| 北海道 | 北海道電力株式会社 |
| 東北 | 東北電力株式会社 |
| 東京 | 東京電力エナジーパートナー株式会社、株式会社JERA |
| 中部 | 中部電力ミライズ株式会社、株式会社JERA |
| 北陸 | 北陸電力株式会社 |
| 関西 | 関西電力株式会社 |
| 中国 | 中国電力株式会社 |
| 四国 | 四国電力株式会社 |
| 九州 | 九州電力株式会社 |

正式名の照合元は[第8回資料8 p26](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/008_08_00.pdf)、
期間の照合元は[第19回資料8 p6](https://www.egc.meti.go.jp/activity/emsc_systemsurveillance/pdf/019_08_00.pdf)です。
第19回資料の新期間列では2026-04-01以降に中部電力株式会社が追加されますが、FY2025へ遡って採用しません。
沖縄はこの対象表にありません。非掲載だけで任意の沖縄事業者のfringeを付与しません。

## HJKS / OCCTO対応候補（今回更新）

調査成果物は `metadata/research/regulatory_dominant_targets.csv`、
`operator_mapping_candidates.csv`、`operator_mapping_review.md` へ整理しました。
原PDFはHTTP403のため未確認で、確認レベルは `official_search_verified` です。

HJKS4ファイルの84表記・105事業者名×エリアを検査しました。
規制候補11組のうち名前・エリア一致は9組（81.82%）、未一致は東京EP・中部ミライズの2組です。
HJKS側では9/105組（8.57%）。exact 8、normalized_exact 1、manual_review 7、unresolved 89です。
これは名前候補の網羅率で、資産join率や最終dominant率ではありません。

東京電力HD/FP/RPとEP、中部電力とミライズは別法人として比較候補に残し、
グループ関係の確認だけで規制帰属を確定していません。confidenceは今回のユーザー指定の5区分を採用します。
全行の規制適用は研究承認待ちです。dominant_flag / fringe_flagは付与していません。
OCCTO発電実績には事業者欄がないため、今回はgenerationとHJKSのjoinも行いません。

未解決96組は `operator_mapping_unresolved.csv`、対象側の網羅率は
`regulatory_target_name_coverage.csv` と `operator_mapping_coverage.json` に保存しています。
詳細・法人関係の公式URL・資料時点・残件は [法人対応調査](../metadata/research/operator_mapping_review.md) を参照してください。

## 検証・保存場所

pytest 72 passed（既存69＋調査schema/候補安全性/対応率3）、ruff通過。
専用worktree `.worktrees/issue-3`、branch `issue/feature-3` に保存し、ユーザー確認までcommit/pushしていません。
