# Issue #15 未対応発電量の追加診断計画（2026-10-16）

1. 既存の677行版候補CSV・QA・long Parquet・raw manifestのSHAを確認し、現在の厳格対応を再現する。677版は探索用参照候補としてのみ使用する。
2. 元のarea×code×正規化plantラベルを維持して未対応plant/unitを棚卸しする。燃種は元列のみを使い、全量・未対応量を月・area・code・燃種別に加算検査する。
3. 全HJKS行を対象に、字形一致、設備語尾・号機・括弧・ローマ数字の診断用表現、fuzzy similarity、code prefix/suffixを候補として列挙する。意味的な変換は元列・既存mappingへ適用しない。
4. A=既存厳格、B=exact/字形normalized exactで一意化できる候補、C=同area・診断用plant基部とunit記号が一致する強い候補までの条件付き上限を比較する。Cのコード不一致は個別に明示し、確認済みmappingとは扱わない。曖昧候補の量はcoverageに入れない。
5. unit/plant/二層方式の長短・観測量・先行研究と限界を報告し、正式採用をせず停止する。raw・既存interim・既存候補CSVは変更しない。

plantラベルに号機情報が入る場合は「ラベル数」と物理的発電所数を混同しない。設備語尾等を除いた診断クラスタは追加の参考表だけに保存する。
