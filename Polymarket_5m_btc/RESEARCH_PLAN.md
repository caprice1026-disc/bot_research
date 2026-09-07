# Polymarket BTC 5m市場における短期価格遅延・Underreaction研究計画

## 1. 研究テーマ

### 1.1 研究目的

Polymarket の BTC Up or Down 5m 市場において、Binance / Coinbase / Hyperliquid / Chainlink 等で観測された BTC 価格情報が、Polymarket CLOB 上の確率価格へ完全かつ即時には反映されず、数百ms〜数秒程度の短期的な underreaction が存在するかを検証する。

最終目的は単なる価格予測ではなく、

q_t = P(UP resolves | I_t)

という自前の fair probability をリアルタイム推定し、q_t - p_Polymarket が手数料・spread・slippage・latency を超える場合だけ取引する quantitative trading system を構築することである。

---

## 2. Polymarket BTC 5m市場の決済仕様

現在の BTC 5分市場は、指定された5分間について Chainlink が生成する BTC/USD TWAP が期間開始時の価格以上なら Up、そうでなければ Down として決済される。

Resolution Source は Chainlink BTC/USD 60-second TWAP Data Stream であり、Binance 等の他取引所の spot 価格そのものではない。

研究上の関係は次のとおり。

- Binance BTC / Coinbase BTC / Hyperliquid BTC: 先行情報候補
- Chainlink BTC/USD 60s TWAP: 実際の resolution source
- Polymarket UP/DOWN CLOB: 市場参加者が付けた確率

---

## 3. 研究仮説

### H0: Efficient Market

外部市場で BTC 価格が動いた時点で、Polymarket 価格も即座に適切な確率へ更新される。

q_t ≈ p_t であり、手数料控除後の edge は存在しない。

### H1: Polymarket Underreaction

Binance / Coinbase / Hyperliquid 等で新しい価格情報が発生した後、

external price → Chainlink → Polymarket

の伝播に遅延が存在する。

例:

- t = 0ms: Binance +$25
- t = 80ms: Coinbase +$24
- t = 250ms: Hyperliquid +$26
- t = 600ms: Chainlink TWAP 変化
- t = 1.8sec: Polymarket UP 0.61 → 0.65

### H2: Market Underestimates Conditional Resolution Probability

Polymarket UP ask が p_ask = 0.65 で、resolver distance、残存時間、volatility、外部市場価格から q_t = 0.73 と推定できるなら edge = 0.08 となる。

実際の売買条件は次を満たさなければならない。

edge > fee + spread + slippage + latency risk + safety margin

---

## 4. 研究方針

最初は「価格予測」と「売買戦略」を分離する。

最初に確認するのは Polymarket の確率そのものより正確な q(t) を作れるかである。次の順で進める。

1. fair probability model
2. mispricing detection
3. execution simulation
4. trading strategy

SL 25%、20秒前 exit、stake $10 などの売買パラメータは初期段階では最適化しない。

---

## 5. 技術スタック

Python 3.12 を研究・データ処理・本番 bot で統一する。

Python 環境管理には uv を使う。

依存関係候補:

- polymarket-client[quant]
- polars
- pyarrow
- duckdb
- numpy
- scipy
- statsmodels
- scikit-learn
- websockets
- httpx
- orjson
- pydantic
- pydantic-settings
- typer
- rich
- plotly

開発用:

- pytest
- pytest-asyncio
- hypothesis
- ruff
- pyright

uv.lock は Git 管理する。旧 py-clob-client ではなく、Polymarket の unified SDK を優先する。ただし、公式仕様と実際に利用可能なパッケージの整合性は実装時に検証する。

---

## 6. データソース

### 6.1 Polymarket CLOB

取得対象:

- full order book
- best bid / best ask
- spread
- price changes
- last trades
- tick size
- market resolution
- token ID
- condition ID

Market Stream の book、price_change、last_trade_price、tick_size_change、best_bid_ask、market_resolved を保存する。

リアルタイム購読は AsyncPublicClient と MarketSpec を使う設計を第一候補とする。

### 6.2 Chainlink Resolver Feed

Polymarket RTDS 経由の Chainlink TWAP を中心にする。

最低限、以下を同時保存する。

- Chainlink TWAP 60s
- Chainlink TWAP 30s

Chainlink event では次を区別して保存する。

- chainlink_observed_ts: payload.timestamp
- chainlink_publish_ts: outer event timestamp
- local_receive_ts
- local_monotonic_ns

RTDS の Chainlink TWAP stream は snapshot / history / reconnect 後 replay を提供しないため、正確な resolver 経路の研究用データは今から自前保存を開始する。

### 6.3 Binance

Historical は Binance 公式 Public Data の BTCUSDT aggTrades を第一候補とする。

保存項目:

- trade timestamp
- price
- quantity
- buyer_is_maker

Live は最低限 BTCUSDT aggTrade と BTCUSDT bookTicker を取得する。

### 6.4 Coinbase

Coinbase Advanced Trade public WebSocket を使う。

endpoint: wss://advanced-trade-ws.coinbase.com

最初は BTC-USD ticker と market_trades を取得し、必要になった段階で level2 と heartbeats を追加する。

### 6.5 Hyperliquid

endpoint: wss://api.hyperliquid.xyz/ws

最初は BTC の trades と bbo を購読し、必要に応じて l2Book を追加する。

### 6.6 Historical Polymarket CLOB

第一候補は pmxt Polymarket Orderbook Archive である。Polymarket CLOB WebSocket market channel の book、price_change、last_trade_price、tick_size_change を含む Parquet archive を利用する。

archive の coverage はダウンロード前に検査し、連続性を仮定しない。取得できない期間を synthetic data で埋めない。

---

## 7. データ保存

初期研究では PostgreSQL や TimescaleDB を主役にせず、Parquet + Polars + DuckDB を使う。

ディレクトリ構成:

- pyproject.toml
- uv.lock
- README.md
- config/research.yaml
- config/live.yaml
- src/btc5m/collectors/
- src/btc5m/ingestion/
- src/btc5m/orderbook/
- src/btc5m/features/
- src/btc5m/models/
- src/btc5m/research/
- src/btc5m/backtest/
- src/btc5m/live/
- data/raw/
- data/normalized/
- data/features/
- data/datasets/
- notebooks/
- reports/
- tests/

Raw event schema:

- source
- symbol
- event_type
- source_event_ts
- source_publish_ts
- local_receive_ts
- local_monotonic_ns
- sequence_id
- price
- bid
- ask
- bid_size
- ask_size
- raw_payload

Parquet は ZSTD を推奨する。partition は source/date/hour 単位を基本とする。

例:

data/raw/polymarket/date=2026-09-07/hour=03/*.parquet

Market master markets.parquet の schema:

- slug
- condition_id
- up_token_id
- down_token_id
- window_start_ts
- window_end_ts
- winning_outcome
- fees_enabled
- fee_rate
- market_created_ts

market_created_ts を5分 window の開始時刻として使用してはいけない。対象 window は slug/title 等から検証して取得する。

---

## 8. Timestamp 設計

受信直後に最低限次を記録する。

- time.time_ns()
- time.monotonic_ns()

source event time による理論上の伝播と、local receive time による実際の売買可能な伝播を分離する。

receive-time で残らない alpha は取引可能な alpha とみなさない。

---

## 9. 時系列 Join とリーケージ防止

Polars の join_asof(strategy="backward") を使用する。

decision timestamp 以前に自分のサーバーへ到着していた情報だけを使う。未来の tick を使う nearest join は禁止する。

全特徴量で feature_timestamp <= decision_timestamp を検査する。

---

## 10. External Composite Price

Binance / Coinbase / Hyperliquid の mid から P_ext を構築する。

最初は単純平均ではなく median を使用する。

log P_ext = median(log P_Binance, log P_Coinbase, log P_Hyperliquid)

外部価格と Chainlink TWAP の gap は次で表す。

L_t = log(P_external / P_ChainlinkTWAP60)

---

## 11. 主な特徴量

### Resolver 系

- chainlink_twap_60
- chainlink_twap_30
- distance_from_start
- distance_pct_from_start
- seconds_left

### External price

- external_mid
- external_return_100ms
- external_return_250ms
- external_return_500ms
- external_return_1s
- external_return_2s
- external_return_5s
- external_return_10s
- external_vs_chainlink

### Volatility

固定 $70 は使わず、次を作る。

- rv_10s
- rv_30s
- rv_60s
- rv_300s
- EWMA volatility

### Normalized resolver distance

z_t = (P_TWAP_t - P_0) / (sigma_hat_t * sqrt(tau))

これは true probability ではなく normalized state variable として扱い、実データで calibration する。

---

## 12. Fair Probability Model

### M0: Empirical Probability Table

seconds_left bucket と z bucket の組合せごとに、

q(z, tau) = UP resolves sample rate

を直接計算する。

### M1: Logistic / Probit

statsmodels GLM で、次のようなモデルを推定する。

logit(q_t) = beta0 + beta1 z_t + beta2 L_t + beta3 r_ext_1s + beta4 r_ext_5s + beta5 RV_30s + beta6 tau

coefficient、standard error、p-value を確認する。

### q_external

Chainlink、Binance、Coinbase、Hyperliquid、time、volatility のみを使い、Polymarket price は入れない。underreaction 検証の中心となる。

### q_full

q_external に Polymarket order book imbalance、spread、depth、last trade を追加する。最終 trading model 候補だが、市場価格を使うため underreaction の一次証拠には q_external を優先する。

---

## 13. Underreaction 検証

### Event Study

external price の一定以上の jump を event とし、100ms、250ms、500ms、1sec、2sec、5sec、10sec 後の Polymarket probability の変化を測る。

event 時点からの impulse response と confidence interval を作成する。

### Predictive Regression

Polymarket probability を log-odds 化する。

l_t = log(p_t / (1 - p_t))

次を推定する。

l_(t+h) - l_t = alpha + beta L_t + gamma X_t + epsilon_t

beta > 0 が out-of-sample でも安定して存在すれば、external market 情報が Polymarket へ完全に織り込まれていない証拠候補となる。

---

## 14. 市場確率と Tradable Edge

研究用 midpoint:

p_mid = (bid + ask) / 2

ただし取引判定では midpoint を使わない。

UP 購入は ask_UP、DOWN 購入は ask_DOWN を使う。

UP:

edge_UP = q_t - ask_UP - cost_UP

DOWN:

edge_DOWN = (1 - q_t) - ask_DOWN - cost_DOWN

現在の crypto market の fee 式は仕様変更の可能性があるため、0.07 を hardcode せず、市場/token ごとの fee 情報を取得する。

---

## 15. Baseline と Execution Simulation

初期 baseline は hold-to-resolution とする。

UP 一株を ask a で購入した場合:

EV = q - a - fee - slippage

20秒前 exit 等は、予測モデル・価格経路・exit timing・bid liquidity を混ぜるため後段で比較する。

Order book ladder:

- ask 0.70: $2
- ask 0.71: $5
- ask 0.72: $20

$10 注文なら $2 @ 0.70、$5 @ 0.71、$3 @ 0.72 として fill price を計算する。

FAK を仮定する場合は decision time + latency 時点の book を使用し、その時点で存在する板だけを食い、残量はキャンセルする。

Latency stress test:

- 0ms
- 100ms
- 250ms
- 500ms
- 1000ms
- 2000ms

---

## 16. 比較するモデル

- S0: 元 repo の ask >= 0.70
- S1: resolver distance only
- S2: Chainlink + volatility
- S3: Chainlink + Binance
- S4: Chainlink + Binance + Coinbase + Hyperliquid
- S5: q_external
- S6: q_full

Edge threshold は 1%、2%、3%、5%、7%、10% 等を比較するが、最終 test set で最適化しない。

---

## 17. 時系列分割・統計

random split は禁止する。

Train → Validation → Test の時系列順を守り、walk-forward を推奨する。

60秒 TWAP や 60秒 volatility feature を使うため、train/test 境界には最大 feature window 分の embargo を置く。

5分市場は日次でクラスタリングされるので、trade 単位 iid を仮定しない。bootstrap unit は day を第一候補とし、HAC / Newey-West standard error も利用する。

複数パラメータ探索による幻覚 alpha を避けるため、block bootstrap、multiple-testing correction、parameter stability を確認する。

---

## 18. 評価指標

### Prediction

PnL より先に次を確認する。

- Brier Score
- Log Loss
- Calibration Error
- Calibration Curve
- AUC（補助）

Market mid = 0.70、q_external = 0.78 のような signal について、edge bucket 別の実際の UP rate を集計する。

### Trading

- net PnL
- PnL / trade
- Sharpe
- Sortino
- max drawdown
- win rate
- profit factor
- number of trades
- fill rate
- average entry edge
- realized edge
- spread cost
- fee cost
- slippage cost
- capacity

Capacity は $1、$5、$10、$25、$50、$100、$250、$500 で比較する。

### Regime

次で分解する。

- volatility: low / normal / high / extreme
- remaining time: 300-180s / 180-120s / 120-60s / 60-30s / 30-10s
- liquidity: spread / top-of-book depth / total depth
- time: UTC hour / weekday / weekend

---

## 19. Historical Research Phase

まず pmxt Polymarket CLOB + Binance historical aggTrades だけで開始する。

目的は external BTC price → Polymarket price の lead-lag の有無を確認すること。

この段階では exact Chainlink TWAP path がないため、resolver-aware exact model とは呼ばない。

不足期間や欠損データを synthetic data で埋めず、coverage と insufficient_data を明示する。

---

## 20. Prospective Research Phase

同時に次を24/7保存する。

- Polymarket CLOB
- Chainlink 30s TWAP
- Chainlink 60s TWAP
- Binance
- Coinbase
- Hyperliquid

5分市場は理論上 288 markets/day、14日で 4032 markets、30日で 8640 markets だが、liquidity filter 等で有効 sample は減る。

---

## 21. Data Quality Tests

毎日自動で次を検査する。

- stream uptime
- missing seconds
- timestamp inversion
- duplicate event
- sequence gap
- negative spread
- crossed book
- stale source
- reconnect count

---

## 22. Noise / Delay / Ablation Test

Signal を random shuffle して backtest する。shuffle 後にも利益が残る場合は future leakage、execution bug、market-selection leakage を疑う。

全 signal を +100ms、+250ms、+500ms、+1s、+2s、+5s 遅らせる。本物の短期 alpha なら delay 増加に伴い PnL が低下するはずである。

Source ablation:

- Chainlink only
- + Binance
- + Coinbase
- + Hyperliquid
- Binance only
- Coinbase only
- Hyperliquid only

---

## 23. Live Architecture

初期は Kafka 等を使わない。

asyncio のプロセスで次を動かす。

- Polymarket collector
- Chainlink collector
- Binance collector
- Coinbase collector
- Hyperliquid collector

in-memory state から Parquet logger と feature engine へ流し、q(t)、signal engine、paper trade へ接続する。

研究後に本番へ移る場合は collector と trader を分離する。Redis Streams、NATS、Kafka は規模が必要になってから検討する。

---

## 24. Paper Trader

Live order book を受信しながら、仮想注文の fill を保存する。

記録:

- signal_ts
- q
- ask
- expected_edge
- send_simulated_ts
- fill_simulated_ts
- price
- shares
- fee
- slippage
- resolution
- pnl

backtest predicted PnL と paper live PnL を比較し、latency、book reconstruction、data gaps、slippage、signal timing の差を調査する。

---

## 25. Trading Decision と安全ガード

q = model.predict(state)

up_edge = q - up_ask - expected_up_fee - expected_up_slippage - latency_buffer

down_edge = (1 - q) - down_ask - expected_down_fee - expected_down_slippage - latency_buffer

edge が threshold を超える場合だけ候補とする。

各 source の now - last_received を監視する。Chainlink、Binance、Polymarket のいずれかが stale なら trade しない。

Binance、Coinbase、Hyperliquid が同方向なら confidence を高め、相反する場合は skip または confidence を下げる。

---

## 26. Research CLI

最小 CLI 候補:

- uv run btc5m collect
- uv run btc5m ingest-pmxt --start 2026-06-01 --end 2026-07-01
- uv run btc5m build-features
- uv run btc5m event-study
- uv run btc5m fit --model logistic
- uv run btc5m backtest --model logistic
- uv run btc5m paper

---

## 27. Reports

実験ごとに次のレポートを生成する。

- reports/01_data_quality.md
- reports/02_market_microstructure.md
- reports/03_lead_lag.md
- reports/04_fair_probability.md
- reports/05_execution_backtest.md
- reports/06_latency_sensitivity.md
- reports/07_paper_trading.md

---

## 28. Go / No-Go 基準

### Gate 1: Lead-Lag

external price が future Polymarket price change を out-of-sample で予測する。

### Gate 2: Probability Edge

q_external が market price との乖離が大きい領域で実際の resolution を有意に予測する。

### Gate 3: Net Edge

fee + spread + slippage + latency をすべて入れても E[PnL] > 0 になる。

### Gate 4: Robustness

利益が特定の1日、threshold、取引所だけに依存しない。

### Gate 5: Paper Trading

live data 上でも backtest と近い fill / PnL になる。

Gate 5 まで通って初めて実資金投入を検討する。

---

## 29. 最初に答える5問

1. 外部 BTC 価格は Polymarket より先に動くか？
2. その差は receive-time でも存在するか？
3. 外部価格 + Chainlink TWAP から market より優れた q(t) を作れるか？
4. q(t) - ask は fee / spread / slippage より大きいか？
5. 実際に注文できる latency を入れても利益が残るか？

---

## 30. 研究の核心

探すものは「BTC が上がったから UP を買う」ことではない。

Information Arrival → Resolver Probability → Polymarket Price の間にある時間的な隙間を検証する。

結果は次の3段階に分けて報告する。

1. statistical edge
2. tradable edge
3. net profitable edge

event-time では存在しても receive-time で消えるなら売買 alpha ではない。gross では利益でも fee 込みで赤字なら、非効率性はあっても収益機会ではない。

---

## 31. 推奨する最終構成

Binance WS、Coinbase WS、Hyperliquid WS から External BTC Composite を作る。

Chainlink 30s TWAP、Chainlink 60s TWAP、Polymarket CLOB の ask / bid / depth を Feature Engine に渡し、Fair Probability q(t)、Edge Calculator を経て fee、spread、slippage、latency buffer を控除する。

Net Tradable Edge > 0 の場合だけ paper または order に進み、それ以外は skip する。

---

## 32. 実装順

### Phase A: collector を最優先

Polymarket、Chainlink 30s / 60s、Binance、Coinbase、Hyperliquid の prospective collector を先に作る。

### Phase B: historical underreaction

pmxt CLOB + Binance historical で lead-lag を調査する。

### Phase C: resolver-aware model

自前収集した Chainlink 入り dataset で q(t) を構築する。

### Phase D: execution-aware backtest

CLOB ladder、fee、spread、depth、slippage、latency、FAK を再現する。

### Phase E: paper trade

real-time で signal だけを出し、実注文はしない。

### Phase F: live

最小サイズで実 fill を測定し、backtest execution model との乖離を再評価する。

---

## 研究上の制約

- 不足した履歴や Chainlink replay を捏造しない。
- local test は remote/live data の検証の代わりにしない。
- event-time の edge と receive-time の tradable edge を分離する。
- 研究結果は exploratory signal であり、十分な検証前の投資助言ではない。
- 実注文・秘密鍵・資金移動は別の明示的な承認なしに実行しない。

