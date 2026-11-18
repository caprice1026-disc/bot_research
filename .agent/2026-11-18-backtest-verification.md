# バックテストの会計と約定精度を実測する

この実行計画はルートの PLANS.md に従う。修正時は合否と未検証の区別を更新する。

## Purpose / Big Picture

同じ入力を再生して同じ取引・残高に戻るか、どの約定をデータから確認できるかを利用者が判断できるようにする。収益や実取引の検証ではない。

## Progress

- [x] (2026-11-18 UTC) main 6db1f50 で既存監査を再実行し、清算漏れを独立作業ツリーで再現。
- [x] (2026-11-18 UTC) 開始時点の清算・Funding・費用の会計を修正し、手計算と対照。
- [x] (2026-11-18 UTC) NautilusTrader（合成と実バー）、HftBacktest（遅延）、板と予測市場を検証し、保存済み48条件も再生。

## Surprises & Discoveries

既存の11月13日監査が「旧ポジションが破綻した時刻に目標ゼロが届く」場合を記録済み。再実行では維持額40、自己資本−2,000、清算計上ゼロ。10月31日研究と11月13日監査結果は現行版の出力とは区別する。

## Decision Log

2026-11-18: 既存の NumPy/Numba 再生器の会計順序を直す。新しい万能シミュレータは作らない。Funding は旧建玉に対して境界で先に処理するという既存研究の規約を維持し、始値の mark（証拠金評価価格）で清算を判定してから任意注文を処理する。旧成果物は更新せず、履歴の基点を固定する。

## Context and Orientation

binance-btcusdt-futures-research/paper_validation_20261031/engine.py の replay は線形無期限先物のポジションを1分足と目標エクスポージャーで再生する。trading-core/src/trading_core は Decimal 型の別のポジション管理実装。Polymarket の残高は契約の確定時払戻しであり、先物の証拠金制ではない。

## Plan of Work

既存再生器に「任意注文より前」の評価点を追加する。入力を固定し、手計算と過去の監査結果を突き合わせる。他エンジンの比較では同じ入力・注文・費用を与える。板データでしか分からない挙動は、バーの結果から推定しない。

## Concrete Steps / Validation and Acceptance

ルートから python -B binance-btcusdt-futures-research/paper_validation_20261031/test_study.py を実行する。100→40 のギャップでロング200を持つとき、目標ゼロの到着は清算を取り消せない。終値の自己資本ゼロだけでなく、清算1回・不足金2,000・再エントリなしを確認する。追加の実行コマンドと出力の読み方は trading-core/README.md に置く。

## Idempotence and Recovery

テストはオフラインで、取引口座・鍵・発注APIを使用しない。過去の論文結果表と manifest は再生成しない。旧不具合を再現する場合は 6db1f50 の別の作業ツリーからその版の監査を実行する。

## Outcomes & Retrospective

修正後、元の反例は清算1回、不足金2,000、最終残高0。実市場バーの48条件には清算がなく、過去PnLとの差は最大7.28e-12 USDTだった。NautilusTrader と既存coreは合成6約定と実バー由来10約定で手数料・時刻・価格・数量・最終残高が一致した。HftBacktestの到着遅延変更は約定の有無を変えた。取引所の実清算、板の待ち順位、実運用の約定率、戦略の利益までは検証していない。
