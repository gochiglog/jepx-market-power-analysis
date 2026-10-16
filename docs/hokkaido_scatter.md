# 北海道の記述用散布図の使い方

[結果と図](../reports/13_hokkaido_scatter/README.md)、[閲覧ノートブック04](../notebooks/04_hokkaido_scatter.ipynb)を先に見てください。
版は`20261016_hokkaido_scatter_v1`です。研究上の採用範囲はRD-019と[契約](../metadata/research/hokkaido_scatter_contract_20261016.yml)に記録しています。

処理は点検済み台帳参照に対して個別法人registryを照合し、北海道の元観測を維持して参照容量差を計算します。
未知の法人名・複数候補・設備保留はunknownです。名前が対象表と一致しないという理由だけでnon_targetにはしません。
制度対象外という属性は競争的な行動の分類ではなく、子会社を親の制度法人へ統合する処理もしません。

参照容量差の和は計算対象設備の部分和です。`R_ref_MW`は計算可能セルが1つ以上ある場合の和、全不能はnullです。
`R_ref_complete_MW`は参照群の全期待設備を計算できる場合のみ値を持ちます。
設備数、期待数、unit日不存在、原出力欠測、容量保留、負値、分類不明、設備集合IDを併記します。
燃種・公表区分の寄与表では期待数は観測された当該区分の数であり、全参照群3設備を各区分に要求しません。
量が空の区分はnullを保持し、4条件を独立の市場観測として加算しません。

主パネルは元北海道市場表の161列を保持し、元の輸入方向連系線列を`import__`で併記します。
元unit表の63列も型と値を保持します。全台帳情報は25行設備表へ保存し、元観測へ日付配列を反復しません。
価格差は北海道−東北、色は系統空容量の東北→北海道の確認済み表示値です。
全FY時刻を保持し、描画に必要な値がない行は理由を持ったまま元パネルへ残します。図の点だけを絞ることとデータの削除を区別します。

再実行は固定版の保存先と別の新規フォルダを指定します。既存出力・既存QAの上書きは拒否します。

```sh
python scripts/build_hokkaido_scatter.py \
  --data-root /path/to/shared-repo \
  --output /path/to/new-data-directory \
  --report-dir /path/to/new-report-directory
pytest -q
ruff check .
```

Parquetは`--output`、PNG/PDF・軽量QAは`--report-dir`に保存します。再実行した版を自動で採用一覧へ追加しません。
固定版の採用一覧は`metadata/hokkaido_scatter_manifest_20261016.json`です。
原本、案A、既存市場・比較表を更新せず、停止量控除・補完・切上げ・回帰を実行しません。
