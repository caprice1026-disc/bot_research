# AIトレーダー論文追補（2026-11-13）

[研究まとめ](RESEARCH_SUMMARY.md)を参照。10月23〜30日号を再点検し、11月6日号のHard to Fool、Dynamic Multi-Pair Trading v3、Beyond Anonymous Order Flowを調査した。

主な結論は、採用審査と外側のリスク制御に参考になる設計がある一方、Hyperliquidの5分売買で利益が出る証拠は得られていない、というもの。既存Binance結果の事後審査は22方式すべて不合格だった。

## 実行

リポジトリルートから実行する。保存済みCSVを読むだけで、ネットワーク・LLM・バックテストの再実行は不要。Python 3.10、NumPy、Pandasを使う。清算反例の実行には旧研究と同じNumbaも必要。

```powershell
$studyPython = 'C:/Users/81905/AppData/Local/Programs/Python/Python310/python.exe'
& $studyPython -X utf8 -B binance-btcusdt-futures-research/paper_review_20261113/audit_existing_results.py
& $studyPython -X utf8 -B binance-btcusdt-futures-research/paper_review_20261113/gap_boundary_audit.py
```

1本目は、旧研究の保存ハッシュ39件を照合したうえで審査CSV・seed再集計・manifestを作る。週単位bootstrapは初期資金で正規化した金額損益を使い、旧報告の日次収益率差の区間とは定義が異なる。10,000回、seed 20261113、下側5%分位。選択補正はしておらず、既に見た結果への審査なので採用認定には使えない。

2本目はコミット `6db1f50` の別作業ツリーで実行する。2026-11-18 に研究用エンジンの境界順序を修正したため、現行版からこの旧監査を実行すると保存済み結果を書き換えず終了コード2で停止する。現行版の回帰テストは [バックテスト検証](../../trading-core/README.md) を参照。

2本目は、旧清算コードの**既知の失敗**を再現する診断である。`known_failure_reproduced` と終了コード1が今回の観測結果。安全性検査に合格したことを示すコマンドではない。

## 成果物

- `results/retrospective_gate_audit.csv`: 22方式への4条件の事後審査。
- `results/audit_manifest.json`: 入力・出力ハッシュ、審査条件、検査した旧成果物数。
- `results/pair_seed_arithmetic.json`: 著者の5seed表の算術再集計。学習再現ではない。
- `results/gap_boundary_audit.json`: 価格ギャップ境界の既知の失敗と保存表の限定的点検。

旧研究のコード・計算済み結果・レポートは上書きしていない。原論文やコードの確認versionとリンクは研究まとめに記載した。Hard to Foolの公開コードは11月10日の版であり、対象週のコードと同一とはしていない。
