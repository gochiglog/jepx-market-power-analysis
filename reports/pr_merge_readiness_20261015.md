# 既存PRのmerge readiness（2026-10-15）

> この文書はマージ前の確認記録です。ユーザー承認後の統合結果は [merge結果JSON](pr_merge_results_20261015.json) を参照してください。

## 結果

PR #7/#9/#11/#12はGitHub REST確認でmergeable=true、mergeable_state=cleanです。
mainへマージしていません。mainのSHAは確認前後とも `2fcd2468430e4414be91f0e7fe6c792fbf1b92f2` です。
GitHub Actionsのcheckは未登録（statusCheckRollup空、workflowなし）。以下のpytest/ruffは研究rawを持つローカルで実行しました。

| PR | base | head | 依存 | pytest | ruff | 競合 |
|---|---|---|---|---:|---|---|
| #7 | main | issue/feature-4 | なし | 69 passed | 通過 | なし |
| #9 | issue/feature-4 | issue/feature-8 | #7 | 114 passed | 通過 | なし |
| #11 | issue/feature-4 | issue/feature-3 | #7 | 73 passed | 通過 | なし |
| #12 | issue/feature-8 | issue/feature-10 | #9 | 129 passed | 通過 | なし |

#9/#11に#7の最新raw改名を取り込み、#12に安定化した#9を取り込みました。
各baseがheadの祖先になっていることも確認しています。
#9更新 `6b819fc`、#11更新 `49a57bd`、#12更新 `d478505` を専用ブランチへpush済みです。

## raw・参照・QA

- raw203件のmanifest・サイズ・SHA256監査は全件一致。改名後の現在local_path203件は全件実在。
- 現在manifestの入力名に旧02_/04_/05_/06_なし。元名・元パスは履歴として保持。
- 時間QAの48件のinput_pathを新パスへ更新し、旧パスはoriginal_input_pathへ保持。
- 時間QAの現在入力129件は全件実在。1,414,286行の元セル・時刻契約・SHA・QA再照合が通過。
- spot入力は公式年度spot_summary_2025.csv。rawパス実在、17,520コマ・元セル・キー一致を実rawテストで確認。
- metadata参照・schemaはpytestで確認。docs/旧Issue #4レポートの旧名称は取得時点の履歴で、現在入力指定には使いません。
- テスト内の旧形式名はsynthetic fixtureの検査用であり、研究rawを指定する現在入力ではありません。
- raw/interim/processedのGit追跡0件。新しい価格感応度QAもrawを書き換えていません。

## 推奨マージ順（ユーザー確認後）

#7を先にマージし、#9/#11のbaseをmainへ変更して再確認。
#9をマージしたら#12のbaseをmainへ変更して再確認します。
現在の順序は #7→#9→#12 および #7→#11です。
今後mainに別変更が入った場合は競合とテストを再確認します。

価格感応度Issue #13の不一致は独立した研究判断事項であり、上記の承認済みspotやrawを修正する理由にはしていません。
上記4PRはマージ可能な状態です。実際のmainマージはユーザー確認を待ちます。
