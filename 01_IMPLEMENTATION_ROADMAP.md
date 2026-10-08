# 実装ロードマップ

## 並行実行の考え方

最速ルートは以下。

### 直列
Issue #1 → #2

### #2完了後に並行
- Track A: #3 dominant/fringe・企業対応表
- Track B: #4 FY2025生データ収集

### その後
#5〜#15

## Issue一覧

### #1 Repo bootstrap
Python環境・ディレクトリ・テスト・lintの初期化。

### #2 Data catalog & schema
すべてのデータソースの粒度・単位・キー・期間を定義。

### #3 Dominant/fringe mapping
発電所・運営事業者を規制上の対象事業者へ対応付ける。

### #4 FY2025 raw data acquisition
2025-04-01〜2026-03-31の生データを収集し、改変せず保存。

### #5 Time normalization
すべてのデータをdelivery date + 1〜48コマへ統一。

### #6 JEPX spot ETL
システム・エリア価格、売買入札量、約定量をlong形式へ。

### #7 Price sensitivity ETL
0.5/1/5GW売買追加時の仮想価格・差分を整形。

### #8 Generation + HJKS ETL
発電実績、認可出力、停止・出力低下を統合。

### #9 Demand & reserve ETL
実需要、翌々日予備率、翌日・当日予備率を整形。

### #10 Interconnector ETL
運用容量、マージン、計画潮流、空容量、市場分断関連変数を整形。

### #11 Asset panel
plant/unit × 30分パネルを作成。

### #12 Firm panel
asset → firmへ集約し、dominant/fringe別指標を作成。

### #13 Market panel
area × 30分パネルを作成し、firm集約値を結合。

### #14 QA audit
欠損・重複・単位・join・異常値を総点検。

### #15 Exploratory figures
研究質問選択用の探索図4枚を作る。ここでは因果推論を主張しない。
