Hyperliquid AI Trader 今後の実装方針・研究ロードマップ

1. 最終的に目指すもの

今後の開発では、現在の "hyperliquid-ai-trader" を単なるTestnet Botから、

«同一のTrader / Reviewer / Risk Engineを、過去市場、Dry-run、Testnetのすべてで再現可能に評価できる研究プラットフォーム»

へ拡張する。

研究の中心課題は「BTCの上昇・下落をLLMが当てられるか」だけではない。

本当に確認したいのは、

«現在の市場情報に対して、取引コストを支払ってでもポジションを取る価値がある局面をLLMが識別できるか»

である。

そのため、評価対象は単純な方向正解率ではなく、

- fee
- spread
- slippage
- funding
- TP / SL
- max hold
- "would_abstain"
- confidence
- Reviewerによる戦略更新

までを含めた費用控除後の最終的なトレーディング方策全体とする。

現在の実装では、Traderが市場特徴量・cost・account・strategy・recent closed tradesを受け取り、Risk Engineがサイズや建玉制限を決定する構造になっている。また、Reviewerが変更できる領域もstrategy内の限定されたフィールドだけに制限されている。これはHistorical Replayへ拡張するうえで非常に良い土台になっている。

---

2. 基本設計思想

2.1 LiveとBacktestで別のTraderを作らない

最も重要な原則とする。

過去データ用に別の「バックテスト戦略」を作るのではなく、

Historical Market Data
        ↓
HistoricalReplayExchange
        ↓
TradingService
        ↓
TraderAgent
        ↓
RiskEngine
        ↓
HistoricalReplayExchange
        ↓
simulated fill / TP / SL / funding

という形にする。

Testnetでは、

Hyperliquid Testnet
        ↓
HyperliquidAdapter
        ↓
TradingService
        ↓
TraderAgent
        ↓
RiskEngine
        ↓
HyperliquidAdapter

となる。

つまり違うのはExchange Adapterだけである。

現在すでに "TradingExchange" Protocolで、市場情報、口座、注文、fills、fundingが抽象化されているため、この境界を利用する。

この設計の意図は、

«「Backtestだけ都合の良いロジックになっていた」»

という最悪の研究バグを防ぐことである。

---

2.2 現在の "dry_run" とHistorical Replayは明確に分ける

現在の "dry_run" は、Risk Engineで注文計画を作ったあと、注文を "simulated" としてSQLiteへ記録して終了する。

実際の過去価格を進めて、

- Entry約定
- TP
- SL
- max hold
- fee
- funding
- slippage
- realized PnL

を再現しているわけではない。

したがって、

dry_run

と

historical_replay

は別モードとする。

dry_run

目的:

- 現在の実市場データでTraderが正常に判断できるか
- Gemini APIが正常か
- Function Callingが正しいか
- 注文を送らずシステム全体を確認する

historical_replay

目的:

- 過去市場を時間順に再生する
- 仮想約定を発生させる
- 仮想口座を更新する
- fee / funding / TP / SLまで含むperformanceを評価する

この区別は今後も維持する。

---

3. 実装全体構成

最終的には以下のような構成を想定する。

hyperliquid-ai-trader/
├─ prompts/
│  ├─ constitution.md
│  ├─ reviewer.md
│  └─ candidates/
│     ├─ trader_v001.md
│     ├─ trader_v002.md
│     └─ ...
│
├─ configs/
│  └─ research/
│     ├─ btc_replay_v1.json
│     ├─ split_v1.json
│     └─ execution_v1.json
│
├─ data/
│  └─ historical/
│     └─ ...                  # Git管理外
│
├─ state/
│  └─ strategy.json
│
├─ src/hyperliquid_ai_trader/
│  ├─ agents.py
│  ├─ features.py
│  ├─ risk.py
│  ├─ runner.py
│  ├─ storage.py
│  ├─ strategy.py
│  │
│  ├─ exchange/
│  │  ├─ base.py
│  │  ├─ hyperliquid.py
│  │  └─ historical.py
│  │
│  └─ research/
│     ├─ dataset.py
│     ├─ clock.py
│     ├─ execution.py
│     ├─ splits.py
│     ├─ manifest.py
│     ├─ candidates.py
│     ├─ baselines.py
│     ├─ replay_runner.py
│     ├─ metrics.py
│     ├─ experiment.py
│     └─ report.py
│
├─ tests/
│  └─ research/
│
└─ results/
   └─ experiments/

市場データそのものは巨大になるためGitへ入れない。

Gitへ保存するのは、

データ取得条件
データmanifest
SHA-256
prompt
config
split
結果
コード

である。

既存の "binance-btcusdt-futures-research" で行っているmanifest / verification方式を、このプロジェクトにも持ち込む。

---

4. Phase 1: Runtime設定をLive環境から分離する

現在の問題

現在の "Settings" は、

wallet
private key
Gemini API key
network
risk settings
model settings
execution settings

をすべて一つに持っている。

さらにTestnet以外を明示的に拒否している。これはLive Botとしては正しい安全設計である。

一方、Historical Replayにはwalletも秘密鍵も必要ない。

ここでHistorical Replayのために、

wallet="dummy"
private_key="dummy"

のようなことを始めるべきではない。

実装

共通部分を切り出す。

概念的には、

@dataclass(frozen=True)
class TradingPolicy:
    coin: str
    leverage: int

    risk_per_trade_pct: Decimal
    max_position_notional_usd: Decimal

    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal

    min_stop_loss_pct: Decimal
    max_stop_loss_pct: Decimal
    min_take_profit_pct: Decimal
    max_take_profit_pct: Decimal

    max_hold_seconds: int

    taker_fee_pct: Decimal
    max_entry_slippage_bps: Decimal

    mandatory_entry: bool

@dataclass(frozen=True)
class ModelPolicy:
    trader_model: str
    reviewer_model: str

    trader_temperature: float
    reviewer_temperature: float

とする。

Live専用部分は、

@dataclass(frozen=True)
class LiveSettings:
    trading: TradingPolicy
    models: ModelPolicy

    wallet_address: str
    private_key: str
    gemini_api_key: str

    network: str = "testnet"

Historical側は、

@dataclass(frozen=True)
class ResearchSettings:
    trading: TradingPolicy
    models: ModelPolicy

    dataset_path: Path
    split_manifest: Path
    execution_model: str

とする。

意図

TradingServiceから、

秘密鍵
wallet
network

という概念を追い出す。

TradingServiceは純粋に、

«「市場・口座・Trader・Reviewer・Risk Engineを使って1サイクル処理するサービス」»

にする。

Testnet安全境界はHyperliquid CLI / LiveSettings側へ残す。

---

5. Phase 2: Historical Dataset Layer

5.1 目的

Historical Replayでは、

«時刻Tで当時利用可能だった情報だけ»

を返さなければならない。

したがってデータには単なるtimestampだけでなく、

event_time
available_at

という概念を持たせる。

例えば1分足なら、

09:00:00 - 09:00:59 の足

を09:00:30時点で使ってはいけない。

完成したあと初めて使用可能になる。

既存のBinance研究でも「足終値が利用可能になる境界」を明確に定義しているため、その思想を流用する。

---

5.2 データ形式

生データと研究結果SQLiteは分離する。

市場データ

Parquetを推奨する。

例えば、

historical/
  candles/
    BTC/
      2026-01-01.parquet

  l2_book/
    BTC/
      2026-01-01/
        00.parquet
        01.parquet

  asset_context/
    BTC/
      2026-01-01.parquet

  funding/
    BTC.parquet

大量の板データはSQLiteへ入れない。

SQLiteは現在と同じく、

run
decision
order
fill
review
strategy
PnL

の証跡に使う。

---

6. Phase 3: HistoricalReplayExchange

これが最重要実装になる。

class HistoricalReplayExchange(TradingExchange):
    ...

を実装する。

現在の "TradingExchange" interfaceをそのまま満たす。

get_market_observation()

get_market_observation(
    coin="BTC",
    now_ms=T,
)

が呼ばれた場合、

Tより未来のデータを絶対に返してはいけない。

返却対象は現行と同じ、

candles
bids
asks
mark
oracle
funding
open_interest
size_decimals

とする。

これにより既存の "build_market_features()" をそのまま通せる。

現在のfeaturesは、

- 1m
- 5m
- 15m
- 60m return
- realized volatility
- ATR
- spread
- order book imbalance
- volume z-score
- funding
- OI

を計算しているため、Historicalでも同じFeature Pipelineを使用する。

---

7. Phase 4: Virtual Clock

Historical Exchangeには仮想時計を持たせる。

exchange.advance_to(timestamp_ms)

を追加する。

"TradingExchange"本体へ追加する必要はなく、

class ReplayExchange(Protocol):
    def advance_to(self, timestamp_ms: int) -> None:
        ...

のようなHistorical専用interfaceでもよい。

意図

現在のBotは5分ごとに動く。

Historical Replayでは、

10:00
10:05
10:10
10:15
...

と仮想時計だけを進める。

sleepはしない。

しかし、その5分間に、

SL hit
TP hit
funding
max-hold close

が起きる可能性がある。

したがって、

exchange.advance_to(next_timestamp)

の際に市場イベントを処理する。

---

8. Phase 5: Execution Simulator

Historical Replayで最も慎重に作るべき部分。

alpha以前に、ここが楽観的なら研究全体が腐る。

Entry

LONGなら原則、

best ask

SHORTなら、

best bid

を基準とする。

mark price即約定扱いにはしない。

現行BotはIOC Entryなので、

HistoricalでもIOCを模倣する。

v1

初期実装では、

top-of-book + conservative slippage

でよい。

v2

L2履歴が十分なら、

注文サイズ
↓
板を消費
↓
VWAP

まで再現する。

現在のposition notionalが比較的小さいならv1とv2の差は小さい可能性があるが、研究基盤として分離しておく。

---

9. TP / SL判定

Entry成立後、

Take Profit
Stop Loss
max hold

を監視する。

理想はtrade / tick dataを使用する。

1m OHLCしかない場合、

High > TP
Low < SL

が同一足で同時に成立することがある。

その場合、

«TPが先だったことにしてはいけない。»

結果を、

TP_FIRST
SL_FIRST
AMBIGUOUS
TIMEOUT

として分類する。

Primary backtestでは、

AMBIGUOUS → SL first

などの悲観的ルールを採用する。

加えて感度分析として、

optimistic result
pessimistic result

を両方出してもよい。

重要なのは、

«Candidateに有利になるよう結果を選ばない»

ことである。

---

10. max-hold

現在、

MAX_HOLD_SECONDS=300

を前提としている。

Historicalでも同じルールを使う。

TPもSLも発火しなかった場合、

entry + 300 seconds

でcloseする。

close priceも、

LONGならbid、
SHORTならask、

を原則とする。

---

11. Fee / Funding / Slippage

すべてPnLに反映する。

gross PnL
- entry fee
- close fee
- slippage
+/- funding
=
net PnL

とする。

現在のレポートも既にfill fee・fundingを集計してnet PnLを計算している。

したがってHistorical Exchange側が、

fills
funding
equity

をLiveと同じ形式で返せば、

既存report pipelineのかなりの部分を再利用できる。

---

12. Phase 6: ReplayRunner

"LocalRunner"とは別に、

class ReplayRunner:
    ...

を作る。

概念的には、

for slot_time in schedule:

    exchange.advance_to(slot_time)

    service.run_once(
        slot=slot,
        scheduled_at_ms=slot_time,
    )

    if reviewer_due:
        service.review_once(...)

とする。

Reviewerも実時間ではなく、

review_interval_seconds

に従った仮想時計で動かす。

これにより、

5分 Trader
2時間 Reviewer

という現在のアルゴリズムそのものをHistorical上へ持ち込める。

---

13. ReviewerはHistoricalでも有効にする

現在のReviewerはstrategyの変更可能範囲がかなり狭く制限されている。

変更可能なのは、

market_hypothesis
active_rules
failure_modes
confidence_calibration

だけである。

この設計は維持する。

Reviewerが、

Risk Engine
position size
leverage
wallet
fee

を変更できるようにはしない。

Historical Replay中のReviewer

例えば、

10:00 Trade
10:05 Trade
...
12:00 Reviewer
12:05 Updated Strategy

という動きは許可する。

これはtest leakageではない。

Reviewerが12:00時点までの結果しか見ていないなら、

«online adaptive algorithmそのもの»

だからである。

---

14. Split設計

Random splitは禁止。

時系列順に分ける。

基本構造は、

Development
        ↓
Walk-forward Validation
        ↓
Locked Test
        ↓
Testnet Forward

とする。

---

15. Development区間

ここだけは自由に見る。

やってよいことは、

prompt改良
strategy仮説
Reviewer prompt変更
confidence semantics変更
feature追加
candidate生成

など。

一般的な機械学習のtrainに相当するが、この研究では「Development」と呼んだ方が混乱が少ない。

モデルweightsを学習しているわけではないからである。

---

16. Walk-forward Validation

例えば、

Fold 1
[ Train ---------------- ][ Val ]

Fold 2
[ Train ------------------------ ][ Val ]

Fold 3
[ Train -------------------------------- ][ Val ]

というexpanding windowを使用する。

各validationはその時点より未来を一切使用しない。

Split boundary

max holdが5分なので、

train最後のpositionがvalidationへ跨がないように、

最低でも、

max_hold_seconds

分のpurgeを行う。

より正確には、

max_hold
+ execution latency

を考慮する。

---

17. Locked Test

最終Test期間は完全に封印する。

Development / Validationを見ながら、

prompt
model
temperature
Reviewer prompt
Reviewer interval
features
confidence gate
execution model

を選ぶ。

その後、

candidate lock

を作る。

例えば、

{
  "candidate_id": "btc-trader-v007",
  "trader_prompt_sha256": "...",
  "reviewer_prompt_sha256": "...",
  "initial_strategy_sha256": "...",
  "model": "...",
  "temperature": 0.2,
  "execution_model": "v1",
  "feature_set": "full",
  "dataset_manifest_sha256": "...",
  "config_sha256": "..."
}

を保存する。

その状態で初めてTestを開く。

---

18. Sealed Test Guard

人間はミスるのでコード側にも柵を作る。

通常の、

replay
walk-forward
optimize

コマンドではTestデータを選択できないようにする。

Testだけ、

sealed-test

という専用commandにする。

さらに、

candidate lock

が存在しない限り実行拒否する。

Test実行後は、

test_consumed_at
candidate_hash
result_hash

を保存する。

これは暗号学的な封印ではなく、

«「うっかりTestをvalidationとして使ってしまう」»

事故を防ぐための研究上の安全柵である。

---

19. TestでLLMを複数回動かす場合

LLMには確率性がある。

現在もTrader temperature 0.7、Reviewer 0.4が設定されている。

したがってTestを一度だけ行うとは、

«API callを一回しか行わない»

という意味ではない。

例えば事前に、

Test replicate count = 5

と決めておき、

同一candidateを独立5試行して、

mean
median
variance
worst case

を評価するのは問題ない。

重要なのは、

«結果を見てから試行回数を変更しない»

ことである。

---

20. LLMのHistorical Knowledge Leakage

これは通常のバックテストより重要な問題。

2026年のLLMへ2024年や2025年のBTC市場を見せる場合、

モデル自体が過去のBTC価格について学習済みである可能性がある。

つまり、

Human leakage

とは別に、

Model pretraining leakage

があり得る。

完全には防げない。

対策

Historical promptには可能な限り、

絶対日時
日付
ニュース
イベント名

を入れない。

さらに、Traderは方向・SL/TPをpercentで判断できるため、

BTC price = $XXXXX

という絶対価格そのものをLLMへ渡す必要性も再検討する。

例えば、

mark
oracle
mid

そのものではなく、

mark/oracle deviation
spread
returns
volatility
book imbalance

のような相対量中心のpromptにする。

こうすることで、

«「この価格なら2025年のあの局面だ」»

という記憶ベースの推論リスクを下げる。

そして最終防衛線になるのがTestnet Forwardである。

Historical Testは、

«人間のprompt最適化に対するOOS»

ではあるが、

«LLM pretrainingに対する完全なOOS»

とは限らない。

これは研究レポートにも明記する。

---

21. Phase 7: Prompt Candidate System

constitutionを直接何度も上書きする方式はやめる。

prompts/candidates/

を作る。

例えば、

trader_v001.md
trader_v002.md
trader_v003.md

とする。

各candidateに、

candidate_id
parent_candidate
変更理由
仮説
作成日時
prompt hash

を付ける。

意図

「何となく文章を変更したら利益が増えた」

を研究結果にしない。

必ず、

«なぜ変更したか»

を残す。

---

22. constitutionとstrategyの役割を分ける

constitutionはなるべく早期に安定させる。

constitutionには、

役割
目的
cost awareness
confidenceの意味
abstention
Risk Engineとの権限境界
Function Calling contract

を書く。

一方、

このregimeではmomentumを重視する
volume spikeでは逆張りを避ける

のような市場仮説は "strategy.json" に持たせる。

つまり、

constitution
= Trading Operating System

strategy.json
= Adaptive Memory

という役割にする。

---

23. Confidenceの意味を明確にする

現在confidenceは0〜1だが、

何に対するconfidenceなのかをさらに明確にすると研究しやすい。

例えば、

«"confidence" は、現在提案しているside / SL / TP / max-hold条件で、費用控除後net PnLが正になる主観確率»

と定義する。

こうするとHistorical結果から、

confidence 0.50-0.60
confidence 0.60-0.70
confidence 0.70-0.80
...

と実際の勝率・expectancyを比較できる。

現在のreportには既にconfidence bucket集計があるので、その基盤をそのまま利用できる。

---

24. Abstentionを研究の中心にする

現在のconstitutionでは、

見送る場合でも、

side
SL
TP
confidence
would_abstain

を返す設計になっている。

これは非常に有用。

Historical Replayでは、実際には発注しなかった場合でも、

«「もし発注していたらどうなっていたか」»

を計算できる。

そこで、

counterfactual_outcomes

を追加する。

例えば、

slot
decision
would_abstain
virtual_entry
virtual_exit
virtual_net_pnl
MFE
MAE

を記録する。

これにより、

良いabstain
→ 損失を回避した

悪いabstain
→ 利益tradeを逃した

が評価できる。

これは今後のprompt改善で非常に重要になる。

---

25. Confidence Gate

Historical Validationでconfidence calibrationが確認できた場合、

Python側のdeterministic gateを追加することも検討する。

例えば概念的には、

if decision.confidence < threshold:
    abstain()

である。

このthresholdはTestを見て選ばない。

Development / Validationだけで決める。

意図

LLMに、

«「自信がないなら慎重になれ」»

と文章でお願いするだけではなく、

最終的な発注条件を機械的に定義する。

---

26. Counterfactual Gate Research

さらに、

estimated round trip cost
confidence
ATR
expected movement

を使って、

trade
abstain

をPython側で決める研究もできる。

ただしこれはPrompt Researchとは別candidateとして扱う。

PromptとGateを同時に変更すると、

どちらが改善原因なのか分からなくなるためである。

---

27. Phase 8: Baseline

LLM同士だけを比較してはいけない。

最低限、

USDC Flat
BTC Buy & Hold
Simple Momentum
Simple Mean Reversion
Random Direction
LLM Trader / Reviewer OFF
LLM Trader / Reviewer ON

を用意する。

特に重要なのは、

LLM Trader / Reviewer OFF
vs
LLM Trader / Reviewer ON

である。

これにより、

«Reviewerが本当に改善しているのか»

を評価できる。

---

28. Ablation Study

さらに、

Book情報なし
Fundingなし
OIなし
Recent Tradesなし
Strategyなし
Reviewerなし
Cost instructionなし

などを比較する。

例えば、

Full Model
vs
No Book Imbalance

で性能がほぼ変わらなければ、

book imbalanceは実際にはLLMのedgeへ寄与していない可能性が高い。

こうして「何が効いたか」を分解する。

---

29. Negative Control

研究としてさらに強くするなら、

feature shuffle
random side
always long
always short

も比較する。

LLMが市場情報を本当に利用しているのか、

単純なlong biasやBTCの上昇傾向に乗っているだけなのかを切り分ける。

---

30. Phase 9: Experiment Registry

各実験を、

experiment_id
candidate_id
dataset
split
fold
trial
model
temperature
prompt hash
strategy hash
git SHA
execution model

で管理する。

既存SQLiteを拡張して、

experiment_runs

テーブルを追加するのが良い。

例えば、

experiment_id
run_id
candidate_id
split_name
fold_id
trial_id
dataset_manifest_hash
config_hash
execution_model_version

を保存する。

現在のSQLiteはすでに、

runs
cycles
decisions
orders
fills
reviews
strategy_versions
patches
events

まで記録しているため、証跡基盤としてかなり強い。

既存構造を壊さず、experiment metadataだけ上へ追加する。

---

31. LLM Response Cache

Historical ReplayではAPI呼び出し数が大きくなる。

さらに、現在の実験でも短時間の集中呼び出しでrate limitが起きている。

したがって、

llm_cache.sqlite

を作ることを推奨する。

キーは概念的に、

prompt_hash
model
temperature
candidate_id
replicate_index

とする。

同一trialの再開時は既存responseを再利用する。

一方、

trial 1
trial 2
trial 3

を独立サンプルとして評価する場合はキャッシュキーを分ける。

---

32. 429をperformanceとして扱わない

Historical Experiment中にrate limitが起きた場合、

429 → abstain

として扱ってはいけない。

candidateの戦略失敗とAPI availabilityは別問題だからである。

そのfoldは、

incomplete

として扱う。

例えば、

required decision coverage = 100%

を満たさない場合、performance比較から除外する。

その後resumeする。

---

33. Candidate Funnel

最初から全期間 × 全candidate × 複数trialをGeminiへ投げるのは非効率なので、段階的にcandidateを絞る。

Stage A
Feature / rule研究
        ↓
Stage B
Development subset
        ↓
Stage C
Walk-forward validation
        ↓
Stage D
Top candidate only
        ↓
Locked Test

Developmentでは、

trend
range
high volatility
low volatility
funding extreme
book imbalance extreme

などの代表局面をサンプリングしてpromptを評価する。

その後、有望candidateのみ全validationへ進める。

---

34. Phase 10: Metrics

Primary Metricは、

Net PnL

だけにはしない。

Profitability

Net PnL
Net return
Expectancy per trade
Expectancy per decision
Profit factor
Win rate

Risk

Max drawdown
Worst trade
Worst fold
PnL volatility

Cost

Gross PnL
Fees
Funding
Slippage
Fee / gross profit ratio
Turnover

Selectivity

Trade count
Abstain count
Abstain rate
Counterfactual abstain PnL

Confidence

Confidence bucket
Observed win probability
Observed net expectancy
Calibration error

Reviewer

Number of reviews
Number of accepted patches
Strategy version count
PnL by strategy version
Patch reversal rate

Execution

Fill rate
Ambiguous TP/SL rate
Average slippage
Timeout rate

Robustness

Fold median
Fold dispersion
Trial dispersion
Worst fold
Performance by market regime

既存レポートはすでに、

gross/net PnL
fee
funding
profit factor
max DD
direction
confidence buckets
would_abstain
strategy versions
MFE/MAE
buy & hold
flat

まで持っているため、これをsingle-run reportとして維持し、その上にcross-runの "experiment_report.py" を追加するのが良い。

---

35. Candidate選択方法

Validationで単純に、

一番Net PnLが高かったPrompt

を採用しない。

例えばPromotion Gateを用意する。

概念的には、

median fold net expectancy > 0
majority of folds profitable
max DD acceptable
fee drag acceptable
sufficient number of trades
not dependent on one exceptional fold
performance stable across stochastic trials

を全部満たすcandidateだけをTestへ進める。

具体的な数値thresholdはDevelopment段階で決め、

Validation / Testを見てから変更しない。

---

36. Phase 11: Prompt Optimization Cycle

実際の開発サイクルは次のようにする。

Development result
        ↓
failure analysis
        ↓
hypothesis
        ↓
new prompt candidate
        ↓
candidate registry
        ↓
Development replay
        ↓
Walk-forward validation

例えば、

低volatility局面でfee負けが多い

という結果が出たら、

いきなり、

ATR < 0.2なら絶対取引禁止

のようなruleを入れない。

まず仮説を作る。

«low-volatilityではexpected movementがcostを超えにくいため、Traderのabstain判断を強くするべきではないか»

その仮説に対応するPrompt Candidateを作る。

この変更理由も保存する。

---

37. Promptを自動最適化するか

初期段階では行わない。

まずは、

Human-designed candidate
+
systematic evaluation

にする。

理由は、自動Prompt Optimizerを最初から入れると、

«Historical PnLを最大化する文章生成器»

になり、非常に速い速度で過学習できるからである。

基盤が完成し、Validation Protocolが安定してから、

train dataだけを見られるPrompt Optimizer

を研究対象として追加するのはあり。

---

38. Phase 12: Test実行

最終candidateが決まったら、

prompt frozen
Reviewer prompt frozen
model frozen
temperature frozen
feature set frozen
risk config frozen
execution model frozen

にする。

そしてTestを実行する。

Test中のReviewer adaptationは許可する。

ただしTest結果を見てPromptを変えた場合、

そのTestは以後、

Validation data

として扱う。

新しいTestは未来側へ移動する。

---

39. Historical Test合格後

すぐに長時間Testnetへ行くのではなく、

Historical
↓
Current-market dry-run
↓
Testnet Canary
↓
Short Testnet
↓
Longer Forward Test

とする。

---

40. Current-market Dry-run

目的はalpha検証ではない。

確認対象は、

現行APIでmarket featuresが生成できる
Geminiがfunction callingできる
prompt schemaが壊れていない
Reviewerが正常
rate limit

である。

---

41. Testnet Canary

現在実装済みのcanaryを利用する。

まず少数tradeで、

IOC
TP
SL
cleanup
fill mapping
fee
position cleanup

を確認する。

現在のBotは異常時に実行停止やcleanupを行う安全設計なので、この部分は維持する。

---

42. Testnetの本当の目的

TestnetのperformanceをHistorical alphaの証明として使いすぎない。

Testnetは主に、

«execution validation»

に使う。

Historical simulatorの、

entry price
slippage
fill timing
TP / SL
fee
latency

と、

Testnet実測を比較する。

つまりTestnetによって、

«Historical Execution Modelが現実離れしていないか»

を検証する。

---

43. Latency Telemetry追加

Testnetへ進む前に、

market snapshot timestamp
Gemini request start
Gemini response
order submit
exchange response
fill time

を記録できるようにする。

これにより実際の、

decision latency
order latency

が分かる。

その分布をHistorical Replayへ戻す。

例えば、

Historical Execution Model v1
latency ignored

v2
empirical decision latency

v3
empirical latency + depth slippage

と進化させる。

---

44. Testnet結果を使った再調整

TestnetでExecution Modelを直すのはよい。

しかし、

Testnetで負けた
↓
Promptを変更
↓
同じ期間を評価

をすると、その期間はValidationになる。

Promptを変更した場合は、

その後の新しい未来期間をForward Testとする。

---

45. 実装するCLI

最終的には次のようなcommand構成が使いやすい。

preflight
dry-run
canary
run-local

research-preflight
replay
walk-forward
compare-experiment
seal-candidate
sealed-test

report

research-preflight

確認対象:

dataset
gaps
manifest
SHA
prompt
candidate
split
config

replay

Development区間で単一candidateを再生。

walk-forward

Validation foldsを順番に実行。

compare-experiment

candidate / trial / foldを集計。

seal-candidate

Test前の設定をlock。

sealed-test

Locked Test専用。

通常commandからTestへアクセスできないようにする。

---

46. テスト実装

Historical関連では特にテストを厚くする。

必須テストは以下。

Future market dataがTより前に見えない
未完成candleが見えない
future fundingが見えない

LONG entryはask側
SHORT entryはbid側

entry feeが正しく引かれる
exit feeが正しく引かれる

TP発火
SL発火
max-hold close

同一bar TP/SL ambiguity

Reviewerが未来tradeを見られない
Recent closed tradesに未来が入らない

foldを跨ぐpositionがない
strategy stateがfold間で意図通りresetされる

rate limitをPnLへ含めない

sealed testをgeneric replayから開けない

同じmanifest + candidate + cached responseなら
再実行結果が一致する

ここはTDDで実装する。

現在のプロジェクト自体もFake Adapterをかなり利用しているため、Historical Exchangeも同じ思想でテストしやすい。

---

47. 実装PRの順番

一度に巨大変更を入れない。

PR 1

Runtime config整理。

TradingPolicy
ModelPolicy
LiveSettings
ResearchSettings

PR 2

Historical dataset schema / manifest。

Parquet
availability timestamp
SHA validation

PR 3

HistoricalReplayExchange基本実装。

market observation
account
virtual clock

PR 4

Execution Simulator。

entry
TP
SL
timeout
fee
funding
slippage

PR 5

ReplayRunner + Historical CLI。

PR 6

Split / Walk-forward / Purge。

PR 7

Experiment Registry / Candidate Registry / LLM Cache。

PR 8

Baselines / Ablations / Counterfactual Abstain。

PR 9

Experiment Report / Confidence Calibration。

PR 10

Candidate Lock / Sealed Test Guard。

PR 11

Testnet latency / execution telemetry。

この順番なら、一つ一つのPRを独立して検証できる。

---

48. 最初の実装MVP

すべてを一気に作る必要はない。

最初のMVPは、

Historical 1m candles
+
simple bid/ask proxy
+
HistoricalReplayExchange
+
TradingService
+
Trader
+
Risk Engine
+
fee
+
TP/SL
+
max hold
+
SQLite

まででよい。

Reviewerも最初はOFFにできるようにする。

まず、

«1つのPromptを過去市場上で最初から最後まで正しくreplayできる»

ことを証明する。

その後、

Reviewer
L2
Funding
OI
Ablation
Walk-forward
Prompt optimization

を足していく。

---

49. 最初に比較する4系統

Historical Replayが完成したら、最初の実験は複雑にしすぎず、

A: Simple deterministic baseline
B: LLM Trader / Reviewer OFF
C: LLM Trader / Reviewer ON
D: LLM Trader / Reviewer ON + abstention

程度から始める。

これで、

LLM自体にedgeがあるのか
Reviewerに意味があるのか
Abstentionがfee負けを減らすのか

を切り分ける。

---

50. この研究で一番重要な問い

最終的には、

«「LLMはBTCを予測できるか？」»

ではなく、

以下の順番で答えを出す。

1.
市場特徴量に単純なedgeはあるか

2.
LLMはその特徴量を利用できるか

3.
LLMはコストを超える局面を識別できるか

4.
confidenceは実際のperformanceと対応するか

5.
abstentionによってfee負けを回避できるか

6.
Reviewerは未知期間でも改善するか

7.
そのedgeは異なる時期・regimeでも残るか

8.
悲観的なexecution assumptionでも残るか

9.
Historicalだけでなく未来のTestnetでも
同じ振る舞いが再現するか

ここまで通って初めて、

«AI Traderとして何らかの再現可能なedgeが存在する可能性がある»

と言える。

---

51. 今回の方針の要約

今回の次の開発フェーズでは、

「Testnet Botを改良する」

のではなく、

「現在のBotを実験可能なTrading Algorithmへ変える」

ことを目的とする。

中心となる新規実装は、

HistoricalReplayExchange
Replay Execution Simulator
Virtual Clock
Historical Dataset Layer
Purged Walk-forward Split
Experiment Registry
Prompt Candidate Registry
LLM Cache
Counterfactual Abstain Evaluator
Cross-run Metrics
Candidate Lock
Sealed Test

である。

既存の、

TradingService
TraderAgent
ReviewerAgent
RiskEngine
TradingExchange Protocol
Strategy Patch
SQLite Evidence Store
Report

はできるだけそのまま使う。

この設計にすると、

Historical
Dry-run
Testnet

のすべてが同じTrading Algorithmを通る。

そして研究全体の流れは最終的に、

BTC市場研究
    ↓
Historical Replay基盤
    ↓
Development
    ↓
Prompt / Strategy candidates
    ↓
Purged Walk-forward Validation
    ↓
Candidate Lock
    ↓
Sealed Historical Test
    ↓
Current-market Dry-run
    ↓
Testnet Canary
    ↓
Testnet Forward Test

となる。

この順序なら、プロンプトの「それっぽさ」ではなく、

«未知期間における費用控除後edge、安定性、選択能力、適応能力»

を中心にAI Traderを評価できる。

そしてHistoricalで良い数字が出たとしても、それを最終結論にはしない。

LLM自身が過去のBTC情報を学習している可能性がある以上、

«最終的なOOS評価はPromptを完全に固定した後のForward Test»

とする。

この考え方を研究全体の根本ルールにする。