# JEPX spot ETL（ロードマップ#6 / GitHub #10）

## 出力と入力

`data/processed/jepx/spot/jepx_spot_FY2025.parquet` を作成しました。
粒度はdelivery_date×period、17,520行・43列です。
元の19列を文字列のまま全件保持し、共通時間/出典7列と正規化数値17列を追加しました。
数値17列はシステム価格1・エリア価格9・数量7（ブロック4を含む）です。

canonicalは `data/raw/jepx/spot/spot_summary_2025.csv`。
同SHAの手動版1件は保持して入力に重ねていません。採用記録は
`metadata/jepx_spot_FY2025_canonical.json` に公式元名・URL・日時・SHA・コピーのパスを保存しました。
異なるSHAの年度候補が増えた場合は自動採用せず確認を求めます。

## 型・単位・元列

価格はJPY/kWh、数量はkWh。単位変換はありません。
価格はdecimal128(38,2)、数量はdecimal128(38,0)で原数値を保存しています。
元の桁・表記は日本語元列に残しています。QAのsummary表示だけはfloat64です。
受渡日はdate32、periodはint8、日時はtimestamp[us,tz=Asia/Tokyo]です。
period48の終了は翌日00:00でもdelivery_dateは元日付を保持します。
元列名との対応は `metadata/variable_dictionary.csv` へ記録しました。

空セルのみ明示nullとし、欠損一覧へ記録します。未定義の非数値表記は元行・元列・値を列挙し、Parquet出力を停止します。
非数値を黙ってNaNにしたり、異常値を削除/winsorizeしたりしません。

## QA結果

- 17,520行、365日、各日48コマ。日付範囲2025-04-01〜2026-03-31。
- period範囲1〜48、重複キー0、不正/欠落コマグループ0。
- 数値parse失敗0、空セル0、全17数値列の欠損率0%。
- raw/processedキー17,520件一致、match率100%、双方の未一致0。
- 元19列の全セルをParquet読み戻しで確認。raw SHA256不変。
- Parquetのschema・値・metadataの書き戻し一致を確認。
- システム価格0.01〜37.51、北海道価格0.01〜80.00 JPY/kWh。全値を保持。
- 入札量・約定量・ブロック数量を含む各列のcount/mean/std/min/25%/50%/75%/maxをQA JSONへ保存。

詳細：`reports/jepx_spot_FY2025_qa.json`。
個別資産/企業のjoin、spread、market splitや経済派生指標は作っていません。

## 実装・実行

`src/jepx_spot.py`、`scripts/etl_jepx_spot.py`、`tests/test_jepx_spot.py`。

```sh
../../.venv/bin/python scripts/etl_jepx_spot.py \
  --data-root /Users/goto/Documents/Uni/04_research_second
```

既存Parquetへの上書きは拒否します。出力はGit管理外です。
pytest 129 passed、ruff通過。実raw integration testで17,520行・元セル・単位・Decimal・時間境界・キー・raw不変を確認。
非数値で出力停止、重複/欠落検知、競合canonical候補で判断待ちになることもテストしています。
研究rawのないCIでは実rawテストのみskipし、汎用の数値parseテストを実行します。

## 残件・次の候補

今回のspotで新たな欠損・parse異常はありません。SJIS/CP932の判別不能性は検査情報として保持しています。
後続の研究判断（HJKS版/有効期間・契約による資産帰属・需要粒度・REI推定列）は未解決のままです。
この結果のユーザー確認後、ロードマップ#7 Price sensitivity ETLを次候補とします。
それまでは価格感応度ETL、generation/HJKS join、headroom、集約、補間、連系線指標、panel、回帰・図へ進みません。
