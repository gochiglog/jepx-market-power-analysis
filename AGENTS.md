# AGENTS.md

このリポジトリは修士研究用の電力市場データ基盤である。研究上の意味を勝手に変更しないこと。

## 言語
- ユーザー向け説明・Issue要約・READMEは原則日本語
- コード識別子は英語でよい
- 不要な英語説明を増やさない

## 絶対ルール
1. `data/raw/` を変更しない
2. raw/interim/processedデータをGitへcommitしない
3. タイムゾーンは `Asia/Tokyo`
4. 1受渡日は48コマ
5. period 1 = 00:00–00:30
6. period 48 = 23:30–24:00
7. kW / kWh / MW / MWhを暗黙変換しない
8. 元列を削除せず、正規化列を追加する
9. join後は必ずmatch率・未一致件数を出力する
10. 未一致行を黙ってdropしない
11. 曖昧な企業名対応を推測で決めない
12. headroom proxyを「余剰電力」「売惜しみ量」と呼ばない
13. 実データからCVの真の主観値を直接観測できると仮定しない
14. 研究上の定義変更が必要なら実装を止めて報告する

## dominant/fringe
baselineは監視委員会の対象事業者一覧を、事業者名×市場エリア×有効期間で適用する。設備容量だけで独自判定しない。

## Git
- 1 Issue = 原則1 branch
- mainへ直接pushしない
- Codexがcommit/pushする場合も明示的なユーザー指示が必要
- 継続指示（2026-10-08）: 区切りのよいタスク完了時にCodexがadd・commit・pushすることをユーザーが明示的に許可済み
- コミットメッセージは `[#<Issue番号>] 実施内容` とする
- Issueの実装・検証完了後は対応するPRを作成し、次のIssueへ進む。PRのマージは明示的な指示がある場合に行う
- commit前にtestsとruffを実行
- 大規模変更は先にplanを提示する

## テスト
最低限: schema validation, 48コマ検証, key uniqueness, unit conversion, join match statistics, interval join tests, date boundary tests

## 処理後の報告
- ユーザーが理解しておくべき重要事項・制約・未確定事項は、判明した時点でその都度チャットで伝える
- 変更ファイル
- 実行したテスト
- テスト結果
- 未解決データ品質問題
- 研究判断が必要な点
