# 08　需要・需給計画・連系線を追加する

[報告書の入口へ戻る](README.md)

需要は1時間と日集計を維持し、30分の需給計画は市場へ接続しました。連系線は方向と重複範囲を整理し、地域の追加輸入能力へ無条件に合算していません。現在のデータ閲覧入口はこの工程の出力です。

## 最初に読む報告

- [demand_supply_direction_20261016.md](demand_supply_direction_20261016.md)

## この工程の記録

以下は作業時点の原記録です。計画書・旧判断・診断表も含むため、採用判断は上の報告と[研究判断ログ](../docs/research_decisions.md)を確認してください。

### 説明・計画

- [demand_supply_direction_20261016.md](demand_supply_direction_20261016.md)

### 数値表・QA・監査

- [demand_daily_consistency_20261016.csv](demand_daily_consistency_20261016.csv)
- [demand_supply_direction_hash_audit_20261016.json](demand_supply_direction_hash_audit_20261016.json)
- [demand_supply_direction_qa_20261016.json](demand_supply_direction_qa_20261016.json)

