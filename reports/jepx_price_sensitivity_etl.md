# 価格感応度ETL 作業報告（ロードマップ#7 / GitHub #13）

## 結果

2026-10-15のユーザー判断RD-008に基づきETLを完成しました。
出力は共有repoの `data/processed/jepx/price_sensitivity/jepx_price_sensitivity_FY2025.parquet`、
17,520行・40列、1,078,606 bytes、Zstandard圧縮です。研究データはGit管理外です。

実績価格はJEPX spot CSVの採用を確定しました。既存spot Parquetの `system_price` は変更していません。
コード・テスト・metadata・既存Parquetがこの列名を参照するため、改名による互換性の変更を避けました。
QA上の役割名 `system_price_market` と同じ実績系列であることを変数辞書とRD-008に記録しました。

## Parquet構造

1行：delivery_date × period。timezoneはAsia/Tokyoです。

| 列群 | 列数 | 内容 |
|---|---:|---|
| raw実数値の全列 | 9 | 日本語元列名・文字列元セル保持 |
| raw差分の全列 | 9 | `difference_raw__` 接頭辞で元名を識別。元セル保持 |
| 時間キー | 5 | delivery_date / period / timestamp_start / timestamp_end / timezone |
| 感応度基準価格 | 1 | system_price_sensitivity_base |
| 仮想価格 | 6 | virtual_price_{sell/buy}_{500/1000/5000}mw |
| JEPX公表差分 | 6 | published_difference_{sell/buy}_{500/1000/5000}mw |
| 出典追跡 | 4 | 実数値・差分のSHA256と元CSV論理行番号 |

正規化価格13列はDecimal128(38,2)、単位は円/kWhです。MWはシナリオ名であり単位換算はしていません。
差分CSVの「システムプライス」元列0.00も削除せず保存しています。
元列対応・原名・取得情報・SHA・非採用コピーはQAとcanonical metadataへ記録しました。

## QA結果

| 検査 | 結果 |
|---|---:|
| 行数・日数 | 17,520行・365日 |
| 毎日48コマ | 全日通過 |
| duplicate key | 0 |
| 実数値・差分の数値parse失敗 | 0 |
| null | 全40列で0 |
| 実数値・差分・spotのキー照合 | 17,520件・100%、未一致各0、drop0 |
| 公表差分と感応度内の差 | 105,120 / 105,120一致 |
| spot基準価格との一致 | 15,249件、87.037671…%（表示87.04%） |
| spot基準価格との不一致 | 2,271件、無修正で保持 |
| 入力raw・同SHA手動コピー | SHA一致・原本無変更 |
| 全登録rawのmanifest・サイズ・SHA監査 | 203件通過 |
| Parquet書き戻し | 全列・全セル・型・schema metadata一致 |

実数値CSV順を維持し、差分CSVはキー照合して元行番号を記録します。
実rawのintegration testでも元18列の全セルと正規化シナリオ全数値を独立して再照合しました。
登録外のOS管理ファイル `.DS_Store` 3件は監査で記録し、raw無変更ルールに従い触れていません。

## 公表差分と既知不一致の扱い

公表差分は差分CSVの数値をそのまま保存しました。
`virtual_price - system_price_sensitivity_base` と105,120件すべて一致しますが、これは公式の符号式を確認した意味ではありません。
`virtual_price - system_price_market` の計算・保存・置換は一切していません。

全コマの比較を [基準価格QA CSV](price_sensitivity_FY2025_baseline_qa.csv) に分離しました。
delivery_date、period、system_price_market、system_price_sensitivity_base、price_baseline_difference、baseline_price_match_flagの6列です。
差の符号は **感応度側基準価格 − spot実績価格**、単位は円/kWhです。
以前の原因調査CSVにはA−Bもありますが、今回のQAはこの式を明示して区別します。

不一致は `completed_with_known_qa_issue` / `blocking=false` としてQA JSONへ記録します。
公式仕様では一致が期待されること、原因未解決であることは変わりません。
用途別原系列保持の承認でETLを再開したのであり、原因の解明や公式訂正とは扱いません。

## decision logと残るTODO

[RD-008](../docs/research_decisions.md) にユーザー判断・理由・注意・適用範囲・再検討条件と公式URLを記録しました。
公式照会は「価格感応度CSVの『実際のシステムプライス』とスポット市場CSVのシステムプライスがFY2025の一部コマで異なる理由をJEPXへ確認する」として残しました。
差分符号式の公式確認も未完了です。いずれも回答待ちをETL・後続研究の進行条件にしません。照会は送信していません。

## 変更・テスト・次の作業

- 実装：`src/jepx_price_sensitivity.py`、`scripts/etl_jepx_price_sensitivity.py`。
- 契約：[実行・出力仕様](../docs/jepx_price_sensitivity_etl.md)、変数辞書に日本語定義と原列対応を追加。
- QA：[集計JSON](price_sensitivity_FY2025_qa.json)、全コマ比較CSV、全raw SHA監査JSON、canonical metadata。
- 既存の事前QAは出力しない診断ツールとして維持し、旧研究判断待ちステータスを解除。原因調査・停止時の報告は履歴として保存し最新判断へのリンクを付加。
- pytest **148 passed**、ruff通過（既存138＋新規10）。日付境界、既知不一致の非停止、原セル保持、差分原値、parse/null停止、重複・欠落停止、差分行順の違い、実raw17,520件、Parquet再読込を検証。

Draft PR #14へ追加し、mainにはマージしません。Issue #13はレビュー待ちとしてopenを維持します。
次はこのPRの確認を推奨します。その後のロードマップは#8 Generation + HJKSですが、今回は着手していません。
