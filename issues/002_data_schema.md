# Issue #2: Data catalog & schema

## 目的
全データソースの粒度・キー・単位・時間仕様を固定する。

## 作業
- `metadata/data_sources.yml`
- `metadata/variable_dictionary.csv`
- 共通キー仕様
- 48コマ仕様
- 単位仕様
- ex ante/ex post区分

## 完了条件
- 全ソースの主キー候補が明示
- すべての数量列に単位
- 日付・コマの共通仕様が文章化
- 不明点は勝手に補完せずTODOとして出力
