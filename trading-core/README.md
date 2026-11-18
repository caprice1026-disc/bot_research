# Trading core

`trading_core` holds the venue-neutral, Decimal-safe pieces used by local trading experiments: target-position delta planning, execution-cost accounting, and an offline single-position simulator.  It has no network dependency and does not import a particular experiment package.

The initial public behavior is exercised through `trading-core/tests` and the fixture runner in `llm-position-management`.  Existing Hyperliquid research modules remain unchanged while their compatibility baseline is preserved.

## バックテスト基盤の検証（2026-11-18）

**結論:** 当面は既存の会計を手計算で監査し、NautilusTrader は同一注文列を通す独立比較に、HftBacktest は板・待ち行列の仮定を変える感度検証に使う。どれも戦略の収益性や実取引所での約定保証ではない。

| 確認した問い | 入力と観測結果 |
|---|---|
| 始値で破綻した建玉を同時刻の手仕舞い注文が救ってしまわないか | 100→40のギャップ、ロング200、元本10,000、維持額40。旧版は清算0回で−2,000。修正版は清算1回、残高0、不足金2,000を別計上し、後続注文は停止。逆方向・Fundingで境界を跨ぐ例・費用・markと約定価格が異なる例もテスト。 |
| 修正によって通常時の過去結果が変わったか | 2026年1–9月のBTCUSDT、16戦略×3コスト条件、393,120分。実質的な終了PnL差なし（最大7.28e−12 USDT）。新旧とも清算0回なので、これは通常経路の回帰検証であり、ギャップ修正の証拠は上の反例。 |
| 他エンジンと約定単位で一致するか | NautilusTrader 1.231.0とPositionAccount。合成6約定は元本10,000→10,009.4。実バー60本・10約定では9,988.845889 USDT、fee 11.429111。両者の時刻・価格・数量・手数料・最終残高が一致。実バーを実気配とはみなさず、ゼロスプレッド・遅延ゼロの比較に限定。 |
| 約定データが違えば何が変わるか | Polymarketの既存top-onlyモデルは最良売り5株に対する6株買いを全拒否。2段目も使う独立IOC参照では合計2.65で6株約定。HftBacktestの同じ合成板・同じmaker注文は到着遅延10msで1単位約定、500msで終了時0。 |

### 追試用の入口

- [清算・Funding・費用の回帰テスト](../binance-btcusdt-futures-research/paper_validation_20261031/test_study.py)と[過去監査](../binance-btcusdt-futures-research/paper_review_20261113/README.md)。旧報告のファイルと最新エンジンの結果を混ぜない。
- [実行精度と市場別の境界、再実行コマンド](validation/EXECUTION_BOUNDARIES.md)と[48条件の数値・入力hash](validation/archived_study_replay.json)。データが揃わなければ収益検証は未実施とする。
- [NautilusTrader の合成・実バー比較と再実行手順](comparisons/README.md)、[HftBacktest の遅延比較](comparisons/hft/README.md)。依存は分離し、通常の trading-core に追加しない。
- [Freqtrade の結果出力](https://www.freqtrade.io/en/stable/backtesting/#backtest-output-file)は、取引記録と設定・戦略を一緒に持ち帰れる点を実験UXの参考とした。ここではFreqtradeを実行していない。市場データ・依存版は別途ハッシュやロックで固定する。

共通化できたのは、同じ入力・目標建玉を与え、約定時刻・符号付き数量・価格・手数料・終残高を照合する範囲。全市場共通の注文状態機械は未実装。次の採用条件は、実際の板・約定・受信時刻をそろえた複数窓で、部分約定、先に並んだ数量、キャンセル競合、費用と誤差を突き合わせること。OHLCVから観測できない順序は作らない。
