# Issue #4: FY2025 raw data acquisition

## 目的
2025-04-01〜2026-03-31の公開データを改変せず保存する。

## 対象
JEPX spot / price sensitivity / OCCTO generation / HJKS unit / HJKS outage / demand / reserve / interconnector / regulatory metadata。

## 作業
- 取得スクリプトまたは再現可能な手順
- manifest作成
- URL・取得日時・sha256記録
- 欠損日レポート

## 完了条件
- 取得可能なFY2025ファイルが揃う
- 欠損・取得不能データが一覧化
- rawはGit管理外
- rawファイルを加工しない
