# 案A：snapshot公表記録を併記する診断データ

本データは参照容量差を変更せず、公表停止・低下記録を条件別に併記する。実停止の認定、市場時点で公表済みの情報の再現、停止損失の控除、最終headroomではない。

## 固定版と構造

版 `20261016_A_v1`。共有repoの `data/interim/occto/public_record_annotation/20261016_diagnostic_A_v1/`。

| ファイル | 粒度・用途 |
|---|---|
| generation_with_snapshot_public_records_FY2025.parquet | 元unitラベル×受渡日×period。8,468,544行。元51列と順序を保持し12列追加 |
| snapshot_original_record_rows.parquet | snapshot×元停止行。2版の66,680行、全元セル・無名末尾セル・SHA・論理行番号を保持 |
| record_contents.parquet | 全15元セルの内容hash別。平均低下量を原文字列と正規化kWで保持。控除しない |
| public_record_contexts.parquet | 各観測に関係する公表記録の参照集合。元行と候補群へ追跡 |
| regional_state_partial_sums.parquet | snapshot×予定端点×area×delivery_date×period×主区分。対象設備の部分和 |
| market_join_by_snapshot_scenario.parquet | snapshot×予定端点×area×delivery_date×period。主区分を横に持つ市場結合用派生表 |

元観測表は1元観測1行。4条件は行を増やさず列で併記する。少数事例の反復表を集計入力に使わない。
公表記録の内容hashと更新候補群hashは公式event IDではない。同内容コピーは参照関係をまとめて識別するが、元行を削除しない。
`update_candidate_group_id` により、現在のコマの参照集合に入らない別予定期間の更新候補も、同じsnapshotの元行表から追跡できる。

## 主区分と予定端点

優先順・日本語の意味・全仕様は `metadata/research/public_record_annotation_contract_20261016.yml`。
設備照合不可、設備期間保留、日時不正、復旧不明、更新候補、複数内容、日付境界、種類別該当、該当記録なしを排他的に割り当てる。contextの理由flagは重複するので足さない。

- 早端は復旧予定日00:00、遅端はその翌日00:00という予定区間の仮定。
- 復旧予定日と開始途中コマは主区分を境界不明に保ち、予定全区間への該当flagだけを条件別に比較する。
- 終了不明の行は停止区間を生成せず、開始後の観測に関係し得る未解決記録として保留する。年度末までの停止ではない。
- `has_full_scenario_record` のTrueは予定シナリオ内に公表行が見つかること。実停止ではない。Falseも停止なしではない。nullは判定不明。他の既知区間の行が見つかればTrueとなり、不明行の存在は主区分/contextに残る。
- 原平均低下量を合算・最大値選択・最新行選択しない。参照容量差から差し引かない。

## 市場パネルへの結合

4条件合計700,800行。1条件は10エリア×365日×48コマ＝175,200行。沖縄を含み、価格の存在を理由にここで落とさない。
**snapshotと予定端点を先に選択する。4条件のまま市場へ結合すると行が増える。**

```python
from pathlib import Path
from src.public_record_annotation import read_market_profile

# 引数は研究側が明示選択する。この例の変数にデフォルトbaselineを設定しない。
region = read_market_profile(
    Path(output_directory) / "market_join_by_snapshot_scenario.parquet",
    snapshot=selected_snapshot,
    scenario=selected_endpoint,
)
joined = market.merge(region, on=["area", "delivery_date", "period"],
                      how="left", validate="one_to_one", indicator=True)
# _merge、未一致、duplicate keyを報告し、暗黙のdropを行わない。
```

参照容量・出力欠測・容量保留・負値・計算設備数を併記する。主区分横持ち列は `<state>__<metric>`。
数量の空区分と全欠測はnull、空区分の件数だけ0。地域の全容量や競争的供給者全体の余力とは扱わない。
国・月・エリアの期間合計で容量/差を加算した値はkWのコマ方向和で、kWhではない。別に観測発電量kWhを持つ。
期間表のdistinct_asset_labelsは同じ設備が別時刻に別主区分へ移るため区分間で足さない。主区分の延べunitセル数は各条件内で排他的。

## 再実行・不変性

```sh
python scripts/annotate_snapshot_public_records.py --data-root /path/to/shared-repo --output /path/to/new-directory
pytest -q
ruff check .
```

既存出力先は拒否する。別出力先の再実行結果は別版としてレビューし、固定manifestを無断で置換しない。
原本・旧固定8成果物・直前診断4Parquetを検査する。元51列の全セル/型/順序、停止全元セル、context参照、地域部分和の再一致を検査し、版manifestへ出力SHAを記録する。

変数定義は `metadata/variable_dictionary.csv`。数値結果は `reports/public_record_annotation_20261016.md`。
