# Hyperliquid AI Trader 今後の実装方針（レビュー版）

## 1. この文書の位置づけ

本書は、`housin.md` と追加提案を統合し、今後の実装順序、研究設計、合格条件を明確にしたレビュー版方針である。目標は単なるバックテスターの追加ではなく、現在の `TradingService`、Trader、Reviewer、Risk Engineを一つのオンライン取引アルゴリズムとして、過去データからTestnetまで同じ境界で評価できるようにすることである。

結論として、次の順序を採用する。

> Historical replay → walk-forward validation → locked test → current-market dry-run → Testnet canary → Testnet forward run

過去データでpromptを改善することには賛成する。ただし研究対象を「文章として優れたprompt」や「BTCの方向正解率」に限定しない。最終的に検証するのは、取引コスト、見送り、リスク制約、Reviewerによる逐次更新、実行失敗を含む**費用控除後の方策全体**に未知データ上のedgeがあるかである。

---

## 2. レビュー結論と重要な修正点

### 2.1 維持する方針

- LiveとbacktestでTraderや特徴量生成処理を二重実装しない。
- `TradingExchange` の境界を利用し、過去データ側を `HistoricalReplayExchange` として実装する。
- `dry_run` と `historical_replay` を明確に別モードとして扱う。
- random splitを禁止し、expanding walk-forward、purge、完全封印testを使う。
- Reviewerには既存のallowlist外を変更させない。
- constitutionは早期に固定し、市場仮説や校正値はstrategyへ置く。
- fee、spread、slippage、funding、TP、SL、max holdをnet PnLへ反映する。
- LLMの確率性を前提に、候補ごとに事前固定した複数replicateを評価する。
- Always abstain、単純ルール、Reviewer OFF、Reviewer ONを最低限比較する。

### 2.2 実装前に補強する方針

`housin.md` の全体像は妥当だが、以下を明示的なgateとして追加する。

1. **データ充足gateを最初に置く。** Replay実装前に、必要列、期間、欠損、時刻意味、L2頻度、fundingの適用時刻をinventory化する。データ不足を約定なしやゼロPnLで隠さない。
2. **決定論的な実行シミュレータをLLM統合より先に完成させる。** Simulatorの誤差をalphaと誤認しないためである。
3. **Research clockを依存注入する。** `time.time()` や実時間sleepが研究経路へ混入しないよう、判断時刻と市場イベント処理時刻を分ける。
4. **観測可能時刻を強制する。** `event_time` に加えて `available_at` を保持し、`available_at <= decision_time` の行だけを返す。
5. **障害率を成績と分離して同時に報告する。** 429、parse error、欠損、注文拒否を損益ゼロとして性能へ混ぜず、運用可能性の指標として別集計する。
6. **Testnet移行条件を数値で固定する。** 良さそうに見えたから進むのではなく、OOS net expectancy、drawdown、replicate安定性、データ品質、実行整合性のgateを満たして進む。
7. **テスト期間の再利用規則を固定する。** 結果を見て変更した場合、その期間は即座にvalidationへ降格し、新しいlocked testを用意する。

---

## 3. 研究目的と評価単位

### 3.1 中心となる問い

中心課題は次である。

> LLMは、BTCの短期的な上下を説明できるかではなく、推定往復コストとリスクを上回る期待値がある局面だけを選別できるか。

この問いを、さらに次の検証可能な問いへ分解する。

- LLM Traderは単純baselineより高い費用控除後expectancyを持つか。
- `would_abstain` は損失tradeを選択的に除外できるか。
- confidenceは実現net expectancyまたは勝率と単調な関係を持つか。
- Python側のcost/confidence gateはprompt内の自制だけより頑健か。
- ReviewerはOOS性能を改善するか、それとも直近ノイズへ過適合するか。
- edgeは特定regime、期間、方向、データ欠損状態だけに依存していないか。
- Historicalでの改善がTestnetのslippage、latency、rejectを含めても残るか。

### 3.2 評価対象

評価対象は個々の回答ではなく、以下を固定したcandidate一式とする。

- Trader promptとhash
- Reviewer promptとhash
- 初期strategyとhash
- model、temperature、生成設定
- feature setと特徴量定義version
- trading/risk policy
- Reviewer有無と更新間隔
- confidence/cost gate
- execution modelとversion
- dataset/split manifest
- code commit SHA
- replicate数と乱数・cache方針

この一式を `candidate lock` として保存し、結果だけを見て一部を差し替えない。

### 3.3 「成功」の定義

方向正解率単独は成功条件にしない。Primary metricは費用控除後のOOS expectancyとし、少なくとも以下を併記する。

- net PnL、gross PnL、fee、funding、slippage
- expectancy / trade
- profit factor
- max drawdownとdrawdown期間
- trade count、turnover、exposure time
- abstain rate
- LONG/SHORT別、regime別、fold別の結果
- confidence bucket別の件数、net expectancy、校正誤差
- replicate間のmean、median、分散、worst case
- ambiguous exitの件数と楽観・悲観差
- API/parse/data/execution failure rate

取引件数が不足した結果や、データ欠損のため取引できなかったゼロPnLは、収益評価の合格として扱わない。

---

## 4. 非目標

初期段階では次を対象外とする。

- Geminiや他の基盤モデルのweights学習
- Mainnetでの実資金運用
- 多銘柄portfolio最適化
- tick完全再現を前提にした高頻度戦略
- LLMがRisk Engine、leverage上限、fee、wallet設定を書き換える仕組み
- prompt自動生成器にsealed testを反復参照させる仕組み
- Binance上の好成績を、そのままHyperliquid上の実行可能性とみなすこと

---

## 5. 基本アーキテクチャ

### 5.1 同一サービス経路

HistoricalとTestnetで共通にする経路は次である。

```text
Market source
    ↓
TradingExchange
    ↓
TradingService.run_once()
    ↓
build_market_features()
    ↓
TraderAgent → validated decision
    ↓
RiskEngine / TradingTools
    ↓
TradingExchange.place_bracket()
    ↓
fills / funding / account / audit store
```

環境ごとの差はExchange、clock、credential組み立てに閉じ込める。prompt context、decision schema、Risk Engine、strategy patch、監査記録の意味は共通にする。

### 5.2 モードの意味

| モード | 市場データ | 注文 | 時計 | 目的 |
|---|---|---|---|---|
| `historical_replay` | 過去データ | 仮想約定 | 仮想時計 | 過去OOS性能の評価 |
| `dry_run` | 現在の実市場 | 注文計画のみ | 実時計 | 接続、prompt、Function Callingの確認 |
| `testnet_live` | Testnet | 実際のTestnet注文 | 実時計 | forward executionの確認 |

現行のdry-runは約定、TP/SL、口座更新を再現するbacktestではないため、名称や実装を流用してHistorical performanceを算出しない。

### 5.3 推奨ディレクトリ

```text
hyperliquid-ai-trader/
├── configs/research/
│   ├── dataset_v1.json
│   ├── split_v1.json
│   ├── execution_v1.json
│   └── experiment_v1.json
├── prompts/candidates/
├── data/historical/                 # Git管理外
├── results/experiments/             # 大容量生成物はGit管理外
├── src/hyperliquid_ai_trader/
│   ├── exchange/historical.py
│   └── research/
│       ├── clock.py
│       ├── dataset.py
│       ├── execution.py
│       ├── splits.py
│       ├── manifest.py
│       ├── candidates.py
│       ├── baselines.py
│       ├── replay_runner.py
│       ├── metrics.py
│       ├── experiment.py
│       └── report.py
└── tests/research/
```

Gitへ保存するのは、取得条件、schema、manifest、hash、config、prompt、split、集計結果、コードである。秘密情報、生データ、大容量の中間生成物は保存しない。

---

## 6. 設定と安全境界

現行 `Settings` はwallet、秘密鍵、Gemini API key、Testnet制約、モデル、risk、executionを一つに持つ。Historicalのためにdummy秘密鍵を与える設計は採用しない。

以下へ段階的に分離する。

```python
@dataclass(frozen=True)
class TradingPolicy:
    coin: str
    margin_mode: str
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
    trader_interval_seconds: int
    review_interval_seconds: int
    taker_fee_pct: Decimal
    max_entry_slippage_bps: Decimal
    mandatory_entry: bool

@dataclass(frozen=True)
class ModelPolicy:
    trader_model: str
    reviewer_model: str
    trader_temperature: float
    reviewer_temperature: float

@dataclass(frozen=True)
class LiveSettings:
    trading: TradingPolicy
    models: ModelPolicy
    wallet_address: str
    private_key: str
    gemini_api_key: str
    network: str = "testnet"

@dataclass(frozen=True)
class ResearchSettings:
    trading: TradingPolicy
    models: ModelPolicy
    dataset_manifest_path: Path
    split_manifest_path: Path
    execution_config_path: Path
```

Testnet限定チェック、署名者認可、秘密鍵読込はlive CLIのcomposition rootへ残す。`TradingService` はwalletやnetworkを直接必要としない状態を目指す。移行中は互換adapterを置き、一度の大規模変更で既存Testnet安全境界を壊さない。

---

## 7. Historical Dataset Layer

### 7.1 データの役割分担

二段階で進める。

1. **Binance長期データ:** regime、feature、threshold、単純baseline、prompt仮説の探索に利用する。
2. **Hyperliquid過去データ:** venue依存のspread、L2 imbalance、mark/oracle、funding、OI、約定条件を含む最終replayへ利用する。

Binanceで発見した仮説は移植候補であり、HyperliquidでのOOS成績とはみなさない。venue間で定義、timestamp、funding、板、価格形成が異なるためである。

### 7.2 最低限必要なデータ

- BTC tradeまたは1分OHLCV
- L2 book snapshotまたは少なくともbest bid/askとsize
- mark price
- oracle price
- funding rateと適用イベント
- open interest
- price/size precision情報
- 取得元、期間、欠損、schema version

各系列は次を持つ。

- `event_time`: 市場で事象が発生した時刻
- `available_at`: Botがその値を利用可能になった最初の時刻
- `source`: データ取得元
- `ingested_at`: 取得処理時刻

1分足は足の開始時ではなく、足が確定して利用可能になった時刻を `available_at` とする。意思決定時刻より未来のbar、後日修正値、未来のfunding確定値を返さない。

### 7.3 データ品質gate

実装着手時にdataset profilerを用意し、次を機械検証する。

- timestampの単調性、重複、timezone
- intervalごとの期待件数と欠損率
- OHLC整合性と非正値
- bid/askの存在、crossed book、異常spread
- mark/oracle/funding/OIの欠損と更新間隔
- decision scheduleごとに必要な61本以上の確定candleがあるか
- split境界、warm-up、purgeに必要な期間があるか
- source fileのSHA-256

欠損補完は列ごとに明示する。価格や板を都合よく未来からbackfillしない。許容不能な観測は`DATA_UNAVAILABLE`としてskipし、件数と時間帯を報告する。

### 7.4 保存形式

市場系列はParquetを基本とし、run、decision、order、fill、review、strategy、PnLなどの監査証跡はSQLiteへ保存する。生データと実験DBを混在させない。

---

## 8. Virtual ClockとReplayの時間意味

Historicalでは実時間sleepを使わない。最低限次のinterfaceを設ける。

```python
class ReplayClock(Protocol):
    @property
    def now_ms(self) -> int: ...

    def advance_to(self, timestamp_ms: int) -> None: ...
```

5分のdecision scheduleと、その間のtrade/L2/funding/exitイベント処理は分ける。

```text
10:00 decision
  ├─ 10:00〜10:05 entry/TP/SL/fundingイベントを時系列処理
  └─ max hold到達時は強制close
10:05 account/fill同期 → 次のdecision
```

同一timestampのイベント順序もversion化する。例えば、既存positionのexit、funding、account更新、新規decisionの順序を明示し、candidateごとに変えない。

Reviewerは仮想時刻の所定intervalでのみ動かし、その時点までにclosedとなったtradeだけを見る。

---

## 9. HistoricalReplayExchange

`HistoricalReplayExchange` は既存 `TradingExchange` contractを満たし、仮想口座、注文、fills、fundingを内部状態として管理する。

主な責務は次である。

- `get_market_observation(coin, now_ms)` で `available_at <= now_ms` の情報のみ返す。
- candles、bids、asks、mark、oracle、funding、OI、size decimalsをliveと同型で返す。
- `place_bracket()` でIOC entryと保護注文を仮想登録する。
- `advance_to()` で市場イベントを時系列処理する。
- `get_account_snapshot()` で仮想cash、position、unrealized PnL、open ordersを返す。
- `get_user_fills()` と `get_user_funding()` でlive同型の証跡を返す。
- `close_position()` とcancel/recoveryの意味をlive contractへ合わせる。

Historical専用操作は `ReplayExchange` Protocolへ分け、live adapterに不要なメソッドを強制しない。

### 9.1 状態遷移

注文とpositionの状態遷移を明示する。

```text
PLANNED → ENTRY_FILLED → PROTECTED → TP_FILLED / SL_FILLED / TIMEOUT_FILLED
        ↘ ENTRY_REJECTED
        ↘ PARTIAL_ENTRY → RECOVERY_CLOSE
```

各遷移はtimestamp、price、size、fee、reason、source event IDを監査記録へ残す。仮想実装でも「成功したことにする」shortcutを作らない。

---

## 10. Execution Simulator

### 10.1 v1: 保守的top-of-book model

- LONG entryはbest ask、SHORT entryはbest bidを基準にする。
- closeはLONGならbid、SHORTならaskを基準にする。
- 設定済みslippageまたは明示的な保守式を不利方向に適用する。
- IOCとして、利用可能流動性または許容slippageを満たさなければreject/partial fillとする。
- entryとexit双方へfeeを課す。
- funding eventをposition保有時だけ適用する。
- precision、minimum size/notionalをliveと同じ規則で処理する。

### 10.2 v2: L2 depth model

L2品質が十分な場合だけ、注文sizeに応じて板を消費しVWAPを算出する。snapshot間の板変化を正確に復元できない場合は、その限界を明記し、v1との差を感度分析する。

### 10.3 TP/SL/timeout

優先データはtrade/tickである。1分OHLCしかない区間で同一足がTPとSLの双方へ触れた場合、順序は断定しない。

- `TP_FIRST`
- `SL_FIRST`
- `AMBIGUOUS`
- `TIMEOUT`

Primary resultでは事前固定した悲観規則（例: `AMBIGUOUS → SL_FIRST`）を使う。併せてoptimistic/pessimistic boundsとambiguous率を出す。候補ごと、結果ごとに有利な規則を選ばない。

max holdはentryの実約定時刻から数える。境界時刻でTP/SLとtimeoutが競合する場合の優先順位もexecution configへ固定する。

### 10.4 PnL恒等式

各tradeで以下が再計算可能でなければならない。

```text
net PnL
= gross realized PnL
- entry fee
- exit fee
+/- funding
```

spreadとslippageは約定価格へ既に反映するため、同じコストを別項目で二重控除しない。レポート上はmid基準implementation shortfallを別途表示してもよい。

---

## 11. Promptとstrategyの管理

### 11.1 役割分担

**constitution（早期固定）**

- 役割と目的
- 費用控除後期待値の重視
- confidenceの定義
- abstentionの意味
- Risk Engineとの権限境界
- Function Calling contract
- 不足情報時の振る舞い

**strategy.json（適応可能領域）**

- market hypothesis
- active rules
- failure modes
- LONG/SHORT confidence calibration

constitutionを相場ごとの自由作文領域にしない。Reviewerによるstrategy patchは現在のallowlistとevidence要求を維持し、risk値や実行モデルを変更させない。

### 11.2 Candidate管理

`prompts/candidates/` にimmutableなcandidateを置き、次のmetadataを記録する。

- candidate ID、parent ID
- 変更箇所
- 変更理由と事前仮説
- 対象failure mode
- 作成日時
- prompt SHA-256

一度評価したcandidateファイルを上書きせず、新しいIDを作る。差分を説明できない大量の文章variationは避け、一度に検証する主要仮説を絞る。

### 11.3 Confidenceとabstention

confidenceは「文章への自信」ではなく、指定sideがコスト控除後に有利である確からしさとしてpromptとschemaで定義する。`would_abstain` は補足説明ではなく、`mandatory_entry=false` 時に実際の見送りへ接続する。

Python側gateはcandidateの一部としてversion化する。例として次を検証するが、閾値はDevelopment/Validationだけで選ぶ。

```text
trade only if:
  would_abstain == false
  confidence >= threshold
  expected_move_bps >= estimated_round_trip_cost_bps * cost_multiplier
```

LLMがexpected moveを安定して返せない場合は、無理に追加せずconfidence gate単独とする。出力項目を増やす場合はschema、prompt、validation、既存runとの互換性を同時に更新する。

---

## 12. Reviewerをオンラインアルゴリズムとして評価する

Test開始時に以下を凍結する。

- Trader/Reviewer prompt
- modelとtemperature
- initial strategy
- review interval
- risk/execution/gate config
- dataset/split

その後、時刻Tまでのclosed tradesだけを根拠にReviewerがstrategyを更新することは許可する。これはtest後の手動調整ではなく、事前定義されたオンライン学習アルゴリズムの実行である。

一方、test結果を見た後にReviewer prompt、更新頻度、allowlist、初期strategyを変更して同じtestを再実行した場合、その期間はlocked testではなくvalidationとなる。

Reviewer評価では次を比較する。

- Reviewer OFF
- Reviewer ON
- 更新なしpatchを返すnegative control
- 可能ならtrade順序やlabelを壊したnegative control

patchごとに根拠trade ID、変更前後、適用時刻、その後の成績を残し、「改善したように見える理由」を追跡可能にする。

---

## 13. データ分割とleakage防止

### 13.1 基本分割

```text
[ Development ][ expanding walk-forward validation ][ purge ][ LOCKED TEST ]
                                                               ↓
                                                        Testnet forward
```

- **Development:** 何度でも参照し、feature、prompt、strategy、gateを開発できる。
- **Walk-forward Validation:** candidate比較と選択に使える。
- **Locked Test:** candidate lock後、事前固定replicate数だけ実行する。
- **Testnet:** 過去データではないforward executionを評価する。

最終6〜8週間をtest候補とする案は妥当だが、固定日数だけで決めない。regime、必要trade数、データ品質をDevelopment情報だけで確認し、split manifest作成後はtestの価格や成績を閲覧しない。

### 13.2 Walk-forward

expanding windowを基本とする。

```text
Fold 1: [ train -------- ][ purge ][ validation ]
Fold 2: [ train ----------------- ][ purge ][ validation ]
Fold 3: [ train -------------------------- ][ purge ][ validation ]
                                                [ locked test ]
```

ここでのtrainはmodel weight学習ではなく、各fold開始時に利用可能なstrategy stateや校正器のfit範囲を表す。最低purgeは `max_hold_seconds + execution_latency_buffer` とする。特徴量warm-upはvalidationの過去側から取得できるが、そのbarをvalidation成績へ含めない。

### 13.3 Leakage check

runnerとdataset層で次をassertする。

- observationの最大`available_at`がdecision time以下
- Reviewer evidenceのclose timeがreview time以下
- fold内のfit対象がvalidation開始前
- positionがsplit境界を跨がない
- locked test command以外からtest partitionを選択できない
- cache keyに未来contextやtest結果が混ざらない

LLMのpretraining leakageは完全には排除できない。Historical promptでは絶対日時、イベント名、ニュースを渡さず、必要性を検証したうえで絶対価格よりreturns、volatility、spread、deviationなどの相対量を優先する。Historical testは人間のprompt選択に対するOOSであり、モデル事前学習に対する完全OOSではないことを報告し、Testnetを最終防衛線とする。

---

## 14. Sealed Test Guard

通常の `replay`、`walk-forward`、`compare` ではtest partitionを指定できないようにする。`sealed-test` は次を要求する。

- 有効なcandidate lock
- dataset/split/config/prompt/codeのhash一致
- 事前宣言したreplicate数
- 未消費のtest ID
- 出力先が新規であること

実行後に以下を追記する。

```json
{
  "test_id": "btc-hl-test-v1",
  "consumed_at": "...",
  "candidate_hash": "...",
  "replicate_count": 5,
  "result_hash": "...",
  "git_sha": "..."
}
```

これは暗号学的秘匿ではなく、偶発的な再利用を防ぐ運用上の柵である。失敗したAPI callだけを都合よく再実行しない。再開可能性が必要な場合は、事前定義したretry/resume規則とcheckpointを使い、完了済みslotを再処理しない。

---

## 15. LLM確率性、cache、失敗の扱い

### 15.1 Replicate

有力candidateは同一期間を複数回実行し、試行数を結果を見る前に固定する。平均だけでなくmedian、分散、worst case、fold間一貫性を評価する。一回の好成績で採用しない。

### 15.2 Response cache

開発コスト削減用cacheは次をkeyへ含める。

- modelと生成設定
- system/constitution hash
- context hash
- tool schema version
- gateway version

cache hitは実験manifestへ記録する。Reviewer ONでは過去の応答がstrategy stateを変えるため、単純な時刻keyではなく完全context hashを使う。replicateの独立性を評価するときは同じcached responseを使い回さない。

### 15.3 API障害

429、timeout、invalid response、schema errorを、相場判断によるabstainと区別する。

- `ABSTAINED_BY_POLICY`
- `SKIPPED_API_ERROR`
- `SKIPPED_DATA_ERROR`
- `RISK_REJECTED`
- `EXECUTION_REJECTED`

主損益と同時にavailabilityを報告する。障害時に未来のslotで過去decisionを補完しない。fallback modelを使用するならcandidate lockへ含め、使用率を報告する。

---

## 16. 比較実験

最低限、同じsplit、execution、risk、cost条件で次の4系統を比較する。

1. **Always abstain:** 取引しない。PnL 0の基準。
2. **Simple baseline:** 5分momentumまたはmean reversion。閾値はDevelopment/Validationで固定する。
3. **LLM Trader / Reviewer OFF:** LLM単体の寄与を測る。
4. **LLM Trader / Reviewer ON:** adaptive memoryの増分寄与を測る。

推奨ablationは次である。

- full feature vs candles/returns only
- cost contextあり/なし
- order book featureあり/なし
- recent closed tradesあり/なし
- confidence gateあり/なし
- mandatory entry vs abstention有効
- Reviewer OFF/ON
- strategy固定/適応

一度に全組合せを走らせず、Developmentで粗く絞り、Validationへ進めるcandidate数に上限を設ける。複数比較で偶然の勝者を選ぶリスクを、試行candidate数とともに報告する。

Negative controlとして、未来参照をしない範囲で意味を失わせた特徴量、ランダムside、またはcontext label permutationを用意する。これらが高成績なら、simulator、split、選択過程の不具合を疑う。

---

## 17. Candidate選択と合格gate

候補を単一PnLで順位付けしない。次の順序で落とす。

1. データ/leakage/監査検証に失敗したrunを除外。
2. API failureやambiguous率が許容上限を超えるrunを除外。
3. 取引件数不足のcandidateを「判定不能」とする。
4. foldごとのnet expectancy、drawdown、安定性を見る。
5. baselineに対する増分とReviewer ON/OFF差を見る。
6. replicateのworst caseと分散を見る。
7. 最後にlocked testへ進めるcandidateを一つに固定する。

具体的な数値閾値は、データinventoryとDevelopment結果を確認してから**testを見る前に**experiment configへ定義する。少なくとも次を含める。

- minimum completed trades
- minimum data coverage
- maximum API/data failure rate
- maximum ambiguous exit rate
- minimum OOS net expectancyまたはbaseline差
- maximum drawdown
- foldのうち正のexpectancyとなる最低割合
- replicate worst-case制約

統計的不確実性はbootstrap等で区間推定してよいが、時系列依存を無視したtrade単位の素朴なiid推定だけに依存しない。

---

## 18. 実装ロードマップ

### Phase 0: Inventoryと研究contract

**実装内容**

- 現行contract、DB証跡、prompt context、cleanup順序を確認する。
- Binance/Hyperliquidデータのcoverageとschemaをinventory化する。
- timestamp、`available_at`、funding、exit優先順位を仕様化する。
- Primary metrics、candidate funnel、sealed test規則をconfig schemaへ落とす。

**完了条件**

- 必須列と欠損率を出すprofilerがある。
- データで再現できないfeature/execution条件が明記されている。
- splitを作る前に必要な期間とwarm-upが計算できる。

### Phase 1: 設定分離とclock注入

**実装内容**

- Trading/Model/Live/Research設定を分離する。
- `TradingService` からcredential依存を外す。
- Research clockを導入する。
- 既存CLIのTestnet拒否、安全チェックを維持する。

**完了条件**

- Historical unit testがwallet、秘密鍵なしでserviceを組み立てられる。
- 既存preflight/dry-run/testnet関連testが退行しない。

### Phase 2: Dataset、manifest、split

**実装内容**

- Parquet loaderとschema validationを作る。
- `event_time` / `available_at` filteringを実装する。
- dataset manifest、SHA-256、coverage reportを生成する。
- expanding split、warm-up、purgeを実装する。

**完了条件**

- future rowを混入させたfixtureを確実に拒否する。
- 同じmanifestから同じsplitが再生成される。
- 境界を跨ぐpositionが存在しない。

### Phase 3: Execution Simulator

**実装内容**

- 仮想account/order/fill/funding ledgerを作る。
- IOC、top-of-book、slippage、fee、TP/SL、timeoutを実装する。
- ambiguous exitの分類と上下限集計を実装する。

**完了条件**

- hand-calculated fixtureとPnLが一致する。
- LONG/SHORT対称性、partial/reject、funding境界をtestする。
- cash + position valueとequityの不変条件が各event後に成立する。

### Phase 4: HistoricalReplayExchangeとReplayRunner

**実装内容**

- `TradingExchange` 互換adapterを実装する。
- 5分Trader、所定interval Reviewerを仮想時計で駆動する。
- resume/checkpoint、完了slot除外を実装する。
- 既存features、TradingTools、store、reportを共用する。

**完了条件**

- Fake Traderによるend-to-end replayが再現可能である。
- 二度実行して決定論部分のledger/hashが一致する。
- liveとhistoricalで同じcontext schemaを生成するcontract testが通る。

### Phase 5: Baseline、metrics、experiment registry

**実装内容**

- 4系統の比較runnerを作る。
- confidence calibration、regime、fold、replicate集計を作る。
- candidate lock、run manifest、artifact hashを保存する。
- failure/coverageとperformanceを分離したreportを作る。

**完了条件**

- Always abstainが取引0、PnL 0として正しく出る。
- 単純baselineはLLM APIなしで再現できる。
- 任意の集計値をfill ledgerから再計算できる。

### Phase 6: Prompt研究とwalk-forward

**実装内容**

- constitutionを安定化しcandidate systemへ移行する。
- cost awareness、abstention、confidence semanticsを順に検証する。
- Reviewer OFFを先に評価し、その後ONの増分を測る。
- candidate funnelに従って候補を削減する。

**完了条件**

- 各candidateに事前仮説とparent差分がある。
- すべてのvalidation結果がmanifestへ紐づく。
- locked testを参照せず最終candidateを一つ選べる。

### Phase 7: Locked test

**実装内容**

- candidate lockを生成する。
- sealed guardを確認する。
- 事前固定replicateだけを実行する。
- 結果を変更不能なartifactとして記録する。

**完了条件**

- test合格gateの判定を自動生成する。
- 不合格時に同じtestでprompt調整を続けない。
- data coverage不足と収益不合格を別理由として報告する。

### Phase 8: Dry-runからTestnetへ

**実装内容**

1. current-market dry-runでcontext、quota、schema、latencyを確認する。
2. Testnet canaryで小回数のentry、protection、cleanupを確認する。
3. 固定candidateで所定期間のTestnet forward runを行う。

**完了条件**

- orphan position/orderがない。
- fill、fee、funding、latencyがレポートへ反映される。
- HistoricalとTestnetのimplementation shortfall差を比較できる。
- Testnet結果を見た変更は新candidateとして次研究cycleへ送る。

---

## 19. 初期MVPの範囲

最初のPR群では、研究機能を一度に完成させない。MVPは次に限定する。

1. credential不要のResearch設定。
2. 小さなfixture datasetと`available_at`付きloader。
3. 保守的top-of-book実行モデル。
4. `HistoricalReplayExchange`。
5. Fake Traderを使う1日分の決定論的replay。
6. fill ledgerからのnet PnL、trade count、drawdown report。
7. Always abstainと単純baseline。

MVPでは実LLMを大量実行しない。まずexecutionとleakage防止をfixtureで証明してからGemini統合、Reviewer、walk-forwardへ進む。

推奨PR順は以下とする。

1. Research config + clock + data contracts
2. Dataset manifest + quality + split/purge
3. Execution simulator + ledger
4. HistoricalReplayExchange + deterministic runner
5. Metrics + baseline + experiment manifest
6. Candidate/cache + LLM replay
7. Reviewer walk-forward + ablation
8. Sealed test guard
9. Dry-run/Testnet telemetry強化

各PRは既存live pathを壊さず、unit fixtureで独立検証可能な大きさにする。

---

## 20. テスト方針

### 20.1 Unit test

- decision時刻より未来のcandle/L2/asset contextを返さない。
- 61本未満、crossed book、欠損intervalを拒否する。
- LONG/SHORTのentry/exit価格が正しい側のbookを使う。
- fee、funding、slippage、partial fill、precisionを正しく反映する。
- TP only、SL only、同一bar両touch、timeoutを分類する。
- max holdとsplit purge境界を確認する。
- Reviewerが未close tradeや未来tradeをevidenceに使えない。
- strategy allowlist外patchを拒否する。
- candidate/hash不一致でsealed testを拒否する。

### 20.2 Integration test

- fixture market → service → Trader → Risk Engine → simulated fill → reportを通す。
- live adapterとhistorical adapterが同型のobservation/account/fillを返す。
- run中断後、完了slotを重複発注せず再開する。
- same input + Fake Traderで同一artifact hashを得る。
- Reviewer OFF/ONで意図した箇所以外の設定が同じである。

### 20.3 Property/invariant test

- position sizeがrisk/notional上限を超えない。
- flatへ戻った後のledgerとrealized PnLが整合する。
- equityがcash、position、fee、fundingと整合する。
- observation/review evidenceに未来timestampが存在しない。
- pessimistic PnLがoptimistic PnLを上回らない。

### 20.4 成果物検証

- manifest記載hashと実ファイルが一致する。
- 全runがcode、candidate、dataset、split、execution versionへ紐づく。
- reportのtrade集計とraw ledger件数が一致する。
- 成功、失敗、skip、abstainの合計が全scheduled slotと一致する。

---

## 21. Testnetで初めて確認すること

Testnetはprompt探索の場ではなく、Historicalで表現しにくい実行差を測る場とする。

- API round-trip latency
- market observationからentry送信までのdecision latency
- ack/fill/protection設定までの時間
- IOC reject、partial fill、response ambiguity
- 想定priceとfill priceの差
- cleanup成功率
- rate limitとscheduler drift
- 実際のfee/fundingとHistorical仮定の差

Testnetの勝ち負けだけでpromptを評価しない。短期間のPnLはsampleが小さく、Testnetの流動性や約定特性もMainnetと一致するとは限らないため、主目的はforward動作とexecution modelの校正である。

---

## 22. 主要リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| future leakage | 架空のedge | `available_at`強制、境界assert、leakage test |
| LLM pretraining leakage | 過去局面の記憶 | 日時・ニュース抑制、相対特徴量、Testnet forward |
| execution optimism | PnL過大評価 | bid/ask、保守slippage、ambiguous悲観評価 |
| venue mismatch | Binance仮説の過大一般化 | Hyperliquid OOS replayを必須化 |
| multiple testing | 偶然のwinner | candidate上限、事前仮説、locked test |
| Reviewer overfit | 直近ノイズ追従 | OFF比較、evidence、更新制限、fold評価 |
| LLM nondeterminism | 再現性低下 | 事前固定replicate、完全manifest、分布評価 |
| API quota/error | 欠測と選択bias | cache、resume規則、failure別集計 |
| データ不足 | ゼロ取引の誤解 | coverage gate、判定不能の明示 |
| simulator/live drift | backtest専用挙動 | 共通service/context、adapter contract test |

---

## 23. 最終的な意思決定規則

Historical locked testを通過しても、直ちに本番運用可能とは判断しない。次をすべて満たした場合にのみTestnetへ進む。

1. データcoverageとleakage検証が合格している。
2. Primary execution modelで費用控除後edgeがあり、単純baselineを上回る。
3. edgeが単一fold、単一方向、少数tradeだけに依存しない。
4. replicateの分散とworst caseが許容範囲内である。
5. max drawdownが事前上限内である。
6. ambiguous、API error、data errorが事前上限内である。
7. Reviewer ONの採否がOFF比較に基づき決まっている。
8. candidate lockと再現manifestが完成している。

Testnetで重大な実行差が見つかった場合は、Historical execution modelを修正し、新しいcandidate/versionとしてDevelopmentから評価し直す。Testnet結果へその場でpromptを合わせ、同じforward期間を成功扱いにしない。

---

## 24. 要約

今後の実装は「過去データでpromptを何度も書き換える」作業ではなく、次の研究基盤を作る作業として進める。

> 同じTradingService、Trader、Reviewer、Risk EngineをHistorical上で時系列再生し、prompt・strategy・gate・Reviewerを一つのオンライン方策としてwalk-forward評価する。

最優先は、`HistoricalReplayExchange` だけを急いで作ることではない。正しい順序は、データinventoryと時間contract、保守的Execution Simulator、Historical adapter、experiment manifestの順である。その上で、Always abstainと単純baselineを含む比較、Reviewer OFF/ON、confidence校正、sealed testを行う。

この設計により、最終判断を「LLMが賢そうな説明をした」から、次へ置き換えられる。

> 未知の時系列データで、実行コスト控除後のedgeが再現し、さらにTestnetのforward executionでもその前提が崩れなかったか。
