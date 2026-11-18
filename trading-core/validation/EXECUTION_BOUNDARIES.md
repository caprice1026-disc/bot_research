# 執行精度と市場別の検証境界

2026-11-18。対象基準コミット: `6db1f50d2314b522d16dc4e5b46bf065643ff93c`。
本資料の数値は明示した人工 fixture の手計算照合であり、実市場の収益・流動性の検証ではない。
API 接続、注文、ウォレット操作、データ取得 runner は実行していない。

## 実装を混同しないための精度レベル

| レベル | 既存の実装 | 保証できること | 保証できないこと |
|---|---|---|---|
| OHLCV / mark | `trading_core.market_data.MarketTick`、`PositionAccount`、`ExecutionCosts` | 固定 bps コスト、Decimal 会計、事前に固定した足内処理規則 | 足内の真の順序、bid/ask、板厚、サイズ依存 impact、maker queue |
| L2 を元にした top-of-book taker | `polymarket-strategy-lab/btc_data.py:Book` と `btc_backtest.py:execute_leg/replay` | snapshot/delta の板復元、鮮度/接続切替/受信途絶の棄却、top ask の実数量条件、脚ごとの固定遅延、指値と費用 | 複数価格の sweep、部分約定、他注文との共有深度消費、実際の市場 impact |
| maker / queue | 上記の研究 runner には実装なし | HftBacktest の合成板で到着遅延のみを変更した別実験を実施 | 接触した指値の約定確率、queue 前方キャンセル、優先順位、cancel race、latency 分布、maker rebate |

Book が全深度を保持していても、features は最良気配の数量しか execute_leg へ渡さない。
5 株の ask の奥に 100 株あっても、6 株注文は全拒否される。これは保守的な小口 taker
モデルであり、一般的な L2 執行エンジンと呼ばない。
また読み取り専用の板を複数の独立仮想注文が使う場合、同じ数量を再利用しない保証はない。

OHLCV の open/high/low/close から真のイベント順序を復元したと主張しない。
HftBacktest を導入するなら、別の「執行感度」比較として同じ L2/trade tape、
数量、tick/lot、fee、latency、queue 仮定を固定し、複数 queue 仮定ごとの範囲を報告する。
[HftBacktest 2.4.4 の独立試験](../comparisons/hft/README.md)では、同じ合成板で
maker 注文の到着遅延だけを変えた。10ms で1単位約定し、500ms では観測終了まで0。
実市場での待ち順位や約定率を較正したものではない。Freqtrade は会計正解の基準に使わない。

## 市場固有の扱い

- perp: trading-core は signed quantity と線形 mark-to-market、明示した Funding を扱う。
  約定単価は数量が 1 でも 1000 でも同じ固定 bps の式。取引所の維持証拠金段階、
  保険基金、ADL、清算料を再現するとは限らない。強制 stop/risk exit と取引所清算を区別する。
- Polymarket BTC: 10 株ずつ ask .44 + slippage .01 で買う fixture。
  各脚の費用は `10 * .45 + round(10 * .07 * .45 * .55, 5) = 4.67325`。
  両脚合計 9.34650、勝者 token のみ 10 ドル payout、損益 .65350。
  手数料は株から控除せず担保の別払い。これは repository の固定 rate .07 を
  検証したもので、現行/歴史的な実取引所 fee schedule が正しいという調査ではない。
- 同じ fixture で第2脚の価格が上昇すれば、最初の脚だけ保有し payout 0、
  損益 -4.67325 になる。完全セットの2脚を原子的に扱わない。
- payout は満期 + 3600 秒（またはより遅い記録済み closed time）まで cash に戻さない。
  PnL を先に計算していても再投資可能 cash とは区別する。
- 実績 runner の settlement lookup は公式勝者を取得・token を照合するが、
  今回の fixture は明示した winner を入力しており、実市場の解決を再検証していない。
- 天気の約定履歴モデル、天気の板モデル、BTC 板モデルは別のデータ品質である。
  未観測の NO ask を 1 - YES で作らない既存テストなどは実行対象に含めた。

## 共通イベント・注文の現状と次の境界

存在する共通値は MarketSnapshot / MarketTick / FundingSettlement / PlanResult /
AccountEvent。時刻の利用可能性と position version による古い intent の拒否はある。
ただしこれらを全市場共通の取引所注文 API と呼ばない。Polymarket replay は独自 dict
と pending 状態を使い、perp の PlanResult をそのまま渡せない。

全市場へ拡張する場合に必要な契約（**未実装**）:
- event の venue、instrument、event-time、receive-time、available-time、sequence、
  schema version と stable tie-break。市場イベント/約定/手数料/決済を区別する。
- order の client id、side、quantity、limit、TIF、reduce-only、submit/ack/fill/cancel/reject
  の状態遷移。fill id と残量を保存し重複・out-of-order を安全に処理する。
- 市場固有 adapter が perp Funding/清算/線形会計と binary token 決済/担保払い fee を担当。
- feature 利用可能時刻と判断/注文/到着時刻を分ける。キャンセルと約定の同時刻競合も固定する。
- 共通 fixture を各 adapter へ通し、入力/設定/版の hash と event/fill ledger を照合する。

## 実行結果と再実行

`test_execution_bounds.py` は本物の既存関数を import し、9 件を検証する。
深度不足・指値・遅延の片脚リスク・手計算の担保/fee/payout・cash 解放時刻・
同一入力再実行の一致/入力非破壊・数量非依存 bps・未来情報の拒否を含む。

リポジトリルートで:

```text
python -B -m unittest discover -s trading-core/validation -p test_execution_bounds.py -v
python -B -m unittest discover -s polymarket-strategy-lab/tests -v
```

最終確認は Windows / Python 3.12.16。Polymarket の requirements.txt に従う分離環境で
既存52件すべて成功。データ依存の1件には、既存ローカルアーカイブから Chicago の
対象30日分の events 30件と resolutions 330件をハッシュ照合して使った（Git 外）。
同じ環境で trading-core の35件および本ディレクトリの17件すべて成功。

## 再現性の限界

追加テストは同一入力の replay を2回 canonical JSON にして SHA256 一致を確認する。
これはそのプロセスでの決定性であり、全 OS/版/実データの同一性証明ではない。
本番の研究実行には原本と部分抽出データの hash、設定 hash、git commit、
Python/依存版、コマンド、timezone、固定 seed、入力順、欠損/除外件数を残す必要がある。
今回、既存の巨大な raw data の全再取得・全 SHA256 照合・全戦略収益再計算は行っていない。


## 追加: 単一スナップショット L2 IOC 参照モデル

`l2_ioc_reference.py` は研究 runner の置換ではなく、新規の独立参照モデル。
`SingleUseL2.from_book(book, as_of=..., max_quote_age=...)` で既存 Book の valid と
受信時刻の鮮度を確認し、Decimal の価格・数量へコピーする。raw OHLCV から
スナップショットを生成しない。元 Book は変更しない。参照モデルは一度 IOC を
計算したら、ゼロ約定でも再使用を拒否する。コピーを再作成したものは独立シナリオ
比較に限り、共有資金/共有深度の複数注文として合算してはいけない。

buy は ask 昇順、sell は bid 降順に数量を消費。limit と等しい価格は許可し、
超えた価格は使わない。残量は IOC canceled に入り、notional と数量加重平均価格を返す。
no fill の平均価格は None。手数料・tick/lot正規化・送信から到着までの変化・
hidden depth・自己 impact・maker は対象外。fee は市場 adapter が別途計算する。
この関数は一時点の見積であり、注文予約/ack/cancel のイベントエンジンではない。
入力 snapshot を作る event tape の網羅性・source clock 品質は呼出側が保証する。

手計算対照（8テスト）:
- ask .44 x2, .45 x5 へ6株 buy: .44 x2 + .45 x4 = 2.68、
  average = 2.68/6、残量0。
- 同じ板へ10株 buy: 7株 fill、notional3.13、3株 cancel。
- buy limit .44: 2株、notional.88、残量4。
- bid .43 x3, .42 x4 へ5株 sell: notional2.13、average .426。
- sell limit .43: 3株 fill、notional1.29、2株 cancel。
- marketable でない指値、同じsnapshot再使用、不正値・crossed/duplicate板を拒否。
- 実際の Book を .44 x5, .45 x100 ask で復元して6株を比較。
  既存 execute_leg は最良 ask の5株しか使わず全拒否。
  新参照モデルは .44 x5 + .45 x1 = 2.65 で全6株 fill。
  これはモデル差の可視化であり、新モデルの方が実市場に正しいと結論しない。
  既存 runner の追加 slippage/fee は新モデルのraw notionalに含めていない。

再実行:
```text
python -B -m unittest discover -s trading-core/validation -p "test*.py" -v
```
初回追加実行: Windows Python3.10、既存9件 + IOC8件 = 17件成功。
この参照モデル自体には maker queue 推定はない。実データでの約定率は検証していない。


## 実データ: 保存済み48条件の現行エンジン再生

`replay_archived_study.py --source-root ../bot_research` をリポジトリルートから実行した。
`archived_study_replay.json` が全48行の旧/新の終値・PnL・各指標差を保持する。

- 元研究の market_1m.npz と performance.csv は旧 run_manifest の SHA256 と一致。
  target_paths.npz は今回 SHA256 を記録したが、旧 manifest はこのファイルを
  hash 対象にしていないため、過去作成時からの不変性までは証明できない。
- 元の market、target、manifest、performance は読み取り専用で開き、
  4ファイルの実行前後hash一致も確認。元 study の出力は上書きしていない。
- market は 2025-10-01 00:00 UTC から2026-10-01未満、525600分。
  評価は2026-01-01から2026-10-01未満の393120分、保存済み判断列78624点。
- 16戦略 × slippage 1/3/5 bps の48条件。swarmは旧研究どおり6口座の合計
  equity と口座平均 exposure に集計し、initial equityも口座数倍にした。
  保存済み実行indexを設定由来の時刻列と照合。学習/特徴量再計算/取得なし。
- 最大絶対PnL差は 7.275957614183426e-12 USDT（CSV浮動小数点読込程度）。
  1e-6 USDT超の変更は0条件。今回の会計残差最大は1.734e-9 USDT。
- 新旧とも清算proxyは全条件0件、新モデルのfloor creditも全条件0。
  したがって実データ48条件の一致は通常経路の回帰確認にはなるが、
  gap清算の修正が実データで検証されたことにはならない。
  その経路は別の手計算stressテストで検証する必要がある。
- 旧CSVは不足金/floor credit列がない。旧ledgerの残差を推定値として別記し、
  観測済み旧不足金の値と取り違えない。
- bootstrap、学習方策の再適合、障害試行の再実行、取引所の実約定との一致、
  L2/makerの収益検証は範囲外。48条件は同じ市場期間を使い統計的に独立ではない。
