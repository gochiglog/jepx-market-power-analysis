# Issue #3: Dominant / fringe mapping

## 目的
発電所・運営会社を規制上の対象事業者へ対応付ける。

## 作業
- 監視委員会資料をmetadataとして登録
- plant/operator名を正規化
- canonical firmを作成
- area×validity periodでdominant_flag付与
- mapping_confidenceを付与

## 完了条件
- mapping CSV完成
- 100%の行が exact/manual_verified/manual_review/unresolved のいずれか
- unresolved一覧を出力
- 容量閾値で勝手に分類しない
