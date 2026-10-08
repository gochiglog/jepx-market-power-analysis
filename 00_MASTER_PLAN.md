# 全体計画：JEPX市場環境・dominant/fringeデータ基盤

## 1. 目的

公開データから以下を再現可能な形で構築する。

1. 市場パネル：`area × 30分コマ`
2. 資産パネル：`plant/unit × 30分コマ`
3. 企業パネル：`firm × area × 30分コマ`

その上で、dominant/fringe別の発電量・利用可能容量proxy・headroom proxyと、
価格、価格感応度、需要、予備率、連系線制約、市場分断を結合する。

## 2. 研究上の二層構造

### 第1層：市場環境
観測可能な現実の市場状態を分析する。

- 需要・需給逼迫
- 発電所停止・出力低下
- dominant/fringeの供給余力proxy
- 地域間連系線の空容量・運用容量
- 市場分断
- システムプライス・エリアプライス
- 価格感応度

### 第2層：戦略・推測
第1層で構築した現実的な市場環境を数理モデルへ与え、
同一市場環境の下でCV（推測構造）の違いが価格・数量・利益・余剰へ与える影響を分析する。

データ基盤構築段階ではCVを推定しない。

## 3. 分析期間

### Baseline
`2025-04-01 <= delivery_date <= 2026-03-31`

まずこの1年間で完成させる。

### 将来拡張
- FY2024以前
- FY2026以降

対象事業者リストや制度定義が変わる場合は、必ず有効期間付きで管理する。

## 4. パネル設計

### Market panel
主キー:
`delivery_date, period, area`

主な変数:
- system_price
- area_price
- area_system_spread
- sell_bid_volume
- buy_bid_volume
- contracted_volume
- price_sensitivity_sell_500mw
- price_sensitivity_sell_1000mw
- price_sensitivity_sell_5000mw
- demand
- reserve_rate_ex_ante
- reserve_rate_ex_post
- market_split_flag
- interconnector_available_capacity
- interconnector_operating_capacity

### Asset panel
主キー:
`delivery_date, period, plant_key, unit_key`

主な変数:
- generation_kwh
- average_output_kw
- authorized_capacity_kw
- outage_flag
- output_reduction_kw
- available_capacity_proxy_kw
- physical_headroom_proxy_kw
- operator_raw
- firm_canonical
- area

### Firm panel
主キー:
`delivery_date, period, area, firm_canonical`

主な変数:
- generation_kw
- available_capacity_proxy_kw
- headroom_proxy_kw
- dominant_flag
- fringe_flag

さらにエリア集約:
- dominant_generation
- fringe_generation
- dominant_headroom
- fringe_headroom

## 5. 探索分析の最初の4図

1. 翌々日広域予備率 vs システム価格感応度
2. fringe headroom proxy vs システム価格感応度
3. 連系線空容量・市場分断 vs エリア－システム価格差
4. fringe headroom × 連系線制約 vs 価格関連指標

この4図で研究質問を確定するのではなく、どの経路がデータ上観測可能かを確認する。

## 6. 先行研究への戻り方

探索分析後、本格的な実証仕様を固定する前にCV・市場支配力・送電制約の主要文献へ戻る。
データを見てから都合のよい仮説を作らない。

## 7. 完成条件

- FY2025の全日・全48コマが監査済み
- 主要データソースの欠損率を報告
- dominant/fringe mappingが出典付き
- unit/plant/firm joinのマッチ率を報告
- すべての派生変数に定義書がある
- QAテストが自動化されている
- 4枚の探索図が再現可能
