# JEPX価格感応度ETL（ロードマップ#7 / GitHub #13）

RD-008（2026-10-15ユーザー確定）により、実績市場価格はspot CSV、価格感応度の基準価格は実数値CSVの別系列として保持します。
spot ETLの既存列 `system_price` は改名せず、比較QAだけで役割名 `system_price_market` を使います。
価格感応度内では公表差分を原値保持し、spot価格で再計算しません。

## 実行

```sh
python scripts/etl_jepx_price_sensitivity.py --data-root /path/to/shared/repo --fiscal-year 2025
```

manifestから公式年度実数値・差分rawを1件ずつ選択します。同SHA手動コピーは保持・入力から除外し、異SHAの候補は自動採用しません。
QA用spotも公式年度rawを読み、別ETLのprocessedファイルに依存しません。
文字コードは全バイト検査で判定し、CP932/Shift_JISの曖昧性を記録します。

## Parquet契約

出力：`data/processed/jepx/price_sensitivity/jepx_price_sensitivity_FY2025.parquet`。
既存出力には上書きせず停止します。研究raw/interim/processedはGit管理外です。
1行は `delivery_date × period`、FY2025は17,520行、40列です。

| 列群 | 列数 | 意味・型 |
|---|---:|---|
| 実数値CSV元列 | 9 | 元日本語列名、全セル文字列で保持 |
| 差分CSV元列 | 9 | `difference_raw__` を付加。元列名対応はQAへ記録。全セル保持 |
| 時間キー | 5 | delivery_date: date32、period: int8、start/end: timestamp(us・Asia/Tokyo)、timezone |
| 基準価格 | 1 | `system_price_sensitivity_base`: Decimal128(38,2)、円/kWh |
| 仮想価格 | 6 | `virtual_price_{sell/buy}_{500/1000/5000}mw`: Decimal128(38,2)、円/kWh |
| 公表差分 | 6 | `published_difference_{sell/buy}_{500/1000/5000}mw`: Decimal128(38,2)、円/kWh |
| 出典 | 4 | 実数値・差分それぞれのSHA256と元論理行番号 |

元小数桁が増えた場合は精度38の範囲でscaleを広げ、勝手に丸めません。
MWは公式シナリオラベルで、追加電力をkW等へ変換する列は作りません。
実数値CSVの順序で出力し、差分CSVはキーで対応させ元行番号を保持します。行をdropしません。
period48は23:30〜翌00:00ですが、delivery_dateは元の受渡日です。
差分rawの「システムプライス」は0.00のまま保持し、価格水準とはみなしません。

## QA

`reports/price_sensitivity_FY2025_qa.json` に時間完全性、重複、parse/null、キー照合、105,120シナリオの内部関係、spot比較、raw不変、Parquet再読込を記録します。
`metadata/jepx_price_sensitivity_FY2025_canonical.json` に3系列の公式元名・URL・日時・SHA・非採用コピーを記録します。

`reports/price_sensitivity_FY2025_baseline_qa.csv` は全17,520キーについて以下の6列です。

- delivery_date
- period
- system_price_market（spot実績価格）
- system_price_sensitivity_base（感応度側基準）
- price_baseline_difference（感応度側基準 − spot実績、円/kWh）
- baseline_price_match_flag（Decimal厳密一致）

既知の基準価格不一致はQAに記録し、停止・削除・上書きをしません。
入力の時間不整合、parse/null異常、重複、SHA不一致、公表差分と内部差の新たな不整合は停止条件です。
`virtual_price - system_price_market` は作りません。

## 未解決事項

公式説明上は両系列が一致するはずですが、2,271コマの原因は未解決です。
差分が仮想価格−感応度基準と一致するのはデータ上の検査で、公式符号式の確認ではありません。
JEPXへの理由・符号式の照会はTODOとし、回答をETL・後続研究の進行条件にしません。照会は送っていません。
Generation + HJKS等は今回の作業範囲外です。
