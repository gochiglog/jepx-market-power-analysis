# Dominant / Fringe 分類ポリシー

## Baselineの定義

dominantは研究者が独自のMW閾値で決めない。

**電力・ガス取引監視等委員会が当該期間・市場区分について「市場支配力を有する可能性の高い事業者」として整理した事業者をbaselineのdominantとする。**

fringeは、同一の市場範囲において観測対象となる供給者のうち、baseline dominantに該当しない事業者群とする。

## 重要な注意

`dominant = 大容量ユニット`、`fringe = 小容量ユニット` ではない。
分類単位は原則として事業者。
さらに対象事業者は市場区分と期間に依存するため、`firm × area × validity period` で管理する。

## 必須列
- firm_canonical
- market_area
- dominant_flag
- valid_from
- valid_to
- source_document
- source_url
- mapping_confidence

## Plant / operator / corporate group
生データの発電事業者名と規制上の対象事業者名が一致しない場合がある。
operator_raw / generation_company / corporate_group / regulatory_entity を区別する。

自動文字列一致だけでdominant_flagを付与しない。

## Mapping監査
- exact
- normalized_exact
- manual_verified
- manual_review
- unresolved

manual_review / unresolvedは分析前に確認する。

## 将来robustness
baseline完成後にのみ、容量シェア、RSI/pivotality proxy、連続的dominance指標を検討する。
