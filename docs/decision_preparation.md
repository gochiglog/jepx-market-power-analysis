# 判断準備用の比較データ

Issue #23。研究方針の検討に使う派生表であり、既存30分表・元頻度需要・案Aを置き換えません。

## 1時間価格と需要

主キーは`area × delivery_date × hour_of_delivery`（0〜23）。FY2025では9×365×24＝78,840行です。
`hour_of_delivery=23`の終端は翌日00:00ですが、受渡日は元の日付を保持します。

- 前半period=`2h+1`、後半period=`2h+2`。元30分表の全列を`halfhour_1__` / `halfhour_2__`へ保持します。
- `area_price`、`system_price_market`、`system_price_sensitivity_base`それぞれに、`__time_mean`、`__minimum`、`__maximum`、`__second_minus_first`、`__absolute_halfhour_difference`を追加します。
- 平均は`(前半＋後半)/2`。等時間の算術平均であり、取引量加重価格でも新しい約定価格でもありません。
- 2コマ未観測・価格nullは、補完せず`__calculation_status`で区別します。平均・最小・最大・差の全てを保留します。
- 元1時間需要の全列を`demand_source__`へ保持し、同じ開始/終了の1時間でone-to-one照合します。30分需要は作りません。
- 沖縄と10エリア総計の各8,760キーは元需要表に残し、比較表の対象外としてQAへ記録します。

`demand_source__demand_hourly_csv_mwh`はCSV仕様上の保存名です。画面の「1時間ごとの最大電力MW」との相違flagを保持します。
「1時間電力量が1時間平均MWと数値的に対応し得る」ことと、「最大電力が測られている」ことは別です。
年次概況の説明も確認しましたが、別帳票から当該CSVの物理的定義を一意に確定していません。
日電力量・日最大電力は元の別表に保持しています。

今回、発電実績・参照容量差・案Aの地域部分和は時間集約しません。変化する設備集合の部分和を同一集合の1時間値として扱いません。
30分の需給計画は前半/後半の元列として参照できますが、実績需要に置換しません。
全国system priceの地域反復と2,271コマの基準価格不一致も原セルのまま残します。

## 方向別連系線比較

3系列とも元行・元列・元型・`source_record_id`・更新時点・設備範囲・公表期間flagを保持します。

| 対象 | 比較表示 | 根拠・制限 |
|---|---|---|
| 系統空容量・翌々日策定 | 順方向は原値×1、逆方向は原値×−1 | 図2-17の順正/逆負の表示軸。空容量だけに限定し、公式確認済み方向だけ適用 |
| 予備率連系線・翌々日 | 順逆別の公表空容量を同値参照 | 全ソース共通の符号反転・絶対値化なし。予想潮流は原定義のまま |
| 予備率連系線・翌日当日 | 順逆別の公表空容量を同値参照 | 計画潮流を実潮流へ置き換えない |

式・対象系列・URL・ページを新列に併記します。原空容量は変更しません。変換後に負値があっても切上げません。
系統CSVの確認済み符号規則は[公式年次概況](https://www.occto.or.jp/houkokusho/2025/files/denryokujukyuu_2024_250910.pdf)物理36・図2-17に基づきます。
年次図の軸を使った表示変換であり、個別機器のFY2025全有効期間や追加輸入可能量を確定する操作ではありません。

単一の送出・受入地域を確認できる場合のみ、同コマの地域価格をmany-to-one参照し、`receiving_minus_sending_price`を追加します。
予備率側は`forward__`と`reverse__`で区別します。多地域フェンス・方向保留では価格差はnullと理由を残します。
一方の地域だけ確認できる場合、その側の価格は参照可能ですが、両端の差は作りません。
リンク値の合算・フェンス統合・制約認定・価格差の因果解釈は行いません。

## HJKS発電事業者欄の棚卸し

全491unitを保持し、既存の診断対応をそのまま参照します。677版は「参照snapshot」であり、取得時点が不明な手動台帳を現在の公開HJKSやFY2025の履歴版と呼びません。

| 主区分 | 意味 |
|---|---|
| scope_checked_unique_reference | 既存診断で設備範囲を点検した一意の参照先 |
| held_scope_or_period_unique_reference | 台帳行は一意だが、期間/設備範囲に既知の保留あり |
| ambiguous_unit_candidates | 設備単位で複数候補 |
| exploratory_plant_pool_only | 同じ発電所候補poolだけ。別号機の事業者を流用しない |
| no_candidate | 既存対応表に候補なし |

「発電事業者」欄の原表記、台帳の全元セル、行ID、SHA、更新日時、取得/登録の区別を保持します。
所有者・運営主体・売電主体・契約供給力の役割は確認済みにせず、FY2025関係の確認flagとは別にします。
補助資料には一般的な事業承継を示す根拠がありますが、個別unitのFY2025全期間の権利・売電・契約を証明したとは扱いません。

主集計の`business_area_exclusive.csv`は各unitの発電量を一度だけ数えます。
`business_area_candidate_ranges.csv`は複数法人に同じunitが含まれ得る候補範囲なので、法人間で加算しません。605候補行も発電実績の行として合算しません。
規制対象法人との字形・空白を除いた名称比較は別表にし、略称展開やグループ帰属を自動適用しません。

## 保存先・再実行

採用する比較用出力は`metadata/decision_preparation_manifest_20261016.json`の7表です。
保存先は`data/processed/comparison/20261016_decision_preparation_v2/`。
開発中のv1は比較用出力の案内対象に含めません。既存固定版や元13表はそのまま保持します。

```sh
python scripts/prepare_research_decisions.py --data-root /path/to/shared-repo --output /path/to/new-directory
pytest -q
ruff check .
```

出力先が既存なら拒否します。元ファイルのSHA、元セル・型、Parquet再読込、結合率・未一致・重複を検証します。
全列の由来と定義は`metadata/decision_preparation_lineage.csv`、日本語定義・単位は変数辞書です。
数値と研究判断用の短い資料は[工程報告](../reports/11_decision_preparation/README.md)へまとめています。
