# FY2025 基本市場パネル：接続診断版

版 `20261016_market_diagnostic_v1`。Issue #17、roadmap #13の基本接続と#10の既存系列ETL。
共有repoの `data/processed/market/20261016_market_diagnostic_v1/`。
採用ファイル・SHAは `metadata/market_panel_FY2025_manifest.json` に限定する。
開発途中の `20261016_market_v1/` は正式成果物に含めない。既存データの削除は行わない。

## 表の構造

| ファイル | 粒度・用途 |
|---|---|
| market_base_FY2025.parquet | 9エリア×365日×48コマ、157,680行・86列 |
| market_hjks_outage_58852_early_FY2025.parquet | 案A58,852版・早端を接続、157,680行・204列 |
| market_hjks_outage_58852_late_FY2025.parquet | 同・遅端、157,680行・204列 |
| market_hjks_outage_7828_early_FY2025.parquet | 案A7,828版・早端、157,680行・204列 |
| market_hjks_outage_7828_late_FY2025.parquet | 同・遅端、157,680行・204列 |
| interconnectors/network_time_contexts_FY2025.parquet | 全国共通17,520時刻。各ソースの原行ID一覧 |
| interconnectors/system_capacity_dayahead2_planned_FY2025.parquet | 連系線×方向×30分の388,896原行と正規化列 |
| interconnectors/reserve_interconnector_nextday2_FY2025.parquet | 翌々日の209,520原行。両方向を列で保持 |
| interconnectors/reserve_interconnector_latest_FY2025.parquet | 翌日・当日の209,472原行。両方向を列で保持 |

価格・ブロック入札・数量の元列を削除しない。重なる感応度側の原列は `sensitivity_source__`、
案Aの元列は `annotation__` で名前空間を分ける。列ごとの日本語名・定義・単位・元列・範囲は
`metadata/variable_dictionary.csv` と `metadata/market_panel_column_lineage.csv`。

`area_price` だけが当該エリアを選んだ価格。`system_price_market` はspot `system_price` の別名で、
元のspot Parquetの列名は変更しない。`system_price_sensitivity_base` と別系列で保持する。
不一致は2,271時刻、地域反復後20,439行。公表差分をspot基準へ再計算しない。
全国共通のsystem price、感応度、入札/約定量、ネットワーク参照は9エリアへ反復した値であり、
地域固有の観測ではない。元の9エリア価格列も全列残すが、当該エリアの選択列と混同しない。

4条件は独立市場観測ではない。分析時は条件を明示選択する。snapshotの最終baselineは未選択。
案Aの数量は「対象設備の部分和」。地域全体・競争的供給者全体の余力ではない。
設備数・既知容量・計算可能容量・出力欠測・容量保留・負値・区分別値をそのまま併記する。
空区分の数量nullと件数0を混同しない。沖縄17,520キー/条件は意図した市場対象範囲差で、A_v1に残る。

## 連系線との接続方法

市場行の `network_context_id` → 時刻文脈表 → 各連系線表の `source_record_id` の参照構造。
時刻文脈への市場結合はareaを展開したone-to-one、元連系線1行は文脈表に1回だけ参照する。
連系線の値を地域値と誤認しないため、全国系統情報の参照として接続し、地域の輸入容量列は作らない。
各リンク・方向・段階の値は参照先で取得でき、ネットワーク全体の単一容量へ合算しない。

```python
from pathlib import Path
import pandas as pd

root = Path(selected_version_directory)
# 研究側がsnapshotと端点を選ぶ。デフォルトbaselineは設けない。
market = pd.read_parquet(root / f"market_{selected_snapshot}_{selected_endpoint}_FY2025.parquet")
contexts = pd.read_parquet(root / "interconnectors/network_time_contexts_FY2025.parquet")
# 同時刻の全国共通参照を展開しても、市場行数が増えないことを検査する。
linked = market.merge(contexts.drop(columns=["delivery_date", "period"]),
                      on="network_context_id", how="left", validate="many_to_one", indicator=True)
assert len(linked) == 157680
print(linked._merge.value_counts(dropna=False))
# 原行の一覧をexplodeして価格回帰へ直結すると市場行が反復する。リンクを先に選択してキーを検証する。
```

系統情報はMW（2025年3月manual表2-8）。順逆の符号は原値を維持する。容量・潮流比から制約状態を作らない。
予備率翌日・当日の潮流は計画値。翌々日は現CSV/2026manualで予想潮流、旧2025manualでは計画潮流。
項目名変更は公式に確認したが、過去値の再計算履歴まで確認できた意味ではない。
現行定義に従う予想潮流数値列と原セルを残し、過去版との意味の差は `flow_semantics` に記録する。
順逆をどの地域の輸入へ割り当てるか、フェンスと個別線の重複範囲は未確定。
全期間の地域別追加輸入能力や実潮流としての利用は保留する。数値の時間接続は完了している。

系統情報は行別最新更新日時、予備率はファイル全体の最新更新日時を別列で保持する。
取得日時・初回公表日時とも区別する。系統情報の策定日は対象日−2日だが、それだけで公表時点の
履歴を再現したとはいえない。予備率の更新日時を各行の公表時刻へ代用しない。
対象時刻より後の更新も原値のまま接続する事後データであり、予測情報集合の再現ではない。
出典・ページ・名称変更/設備変更の注意は `metadata/research/interconnector_connection_review_20261016.yml`。

## 再実行とQA

```sh
python scripts/build_market_panel.py --data-root /path/to/shared-repo --output /path/to/new-directory
pytest -q
ruff check .
```

既存出力先は拒否。raw・固定A_v1・旧診断を変更しない。CSVの文字コードを実判定し、曖昧なCP932/Shift_JISは
曖昧性を記録する。数量はDecimal128(38,6)のMW、未知の非数値は停止。空欄/公式***は理由付きnull。
原セルの全件照合、年度/48コマ/重複/参照ID/書戻し/未使用キー/原本SHAを検査する。
連系線本数の変化を保持する。需要は1時間と日集計のままで、30分へ複製・補間・結合しない。

診断の接続に限定する。停止量の全国控除、最終headroom、資産法人帰属、dominant/fringe、因果効果は作らない。
