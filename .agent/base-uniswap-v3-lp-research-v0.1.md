# Base Uniswap v3 LP研究 v0.1 実行計画

このExecPlanは生きた文書であり、`PLANS.md`の規約に従って、進捗・発見・判断・成果を更新する。目的は、Base WETH/USDC Uniswap v3 0.05%について、狭いレンジと高頻度再配置の利益領域が実データで存在しそうかを、AIなしのC0 event-driven backtesterで再現可能に判定することである。最初に作るものは、本番運用botではなく、データ取得・検証・シミュレーション・レポートの研究基盤である。

## Purpose / Big Picture

実装後、利用者は研究ディレクトリで設定ファイルを指定して、BaseのpoolをFactoryから解決し、Swap/Mint/Burn/Collectとblock/receiptを取得し、同じイベント列をHODL、Full Range、Static Narrow、τ-reset、Threshold Resetへ渡せる。結果にはGross Feeだけでなく、HODL-relative net alpha、LVR診断、gas、inventory swap、price impact proxy、rebalances/day、drawdown、データ品質状態が含まれる。取得範囲が不足する場合は、成功したようなゼロ損益を出さず、`insufficient_data`と理由を返す。

この計画はリポジトリルートの `PLANS.md` と、研究の設計根拠である `docs/superpowers/specs/2026-08-09-base-uniswap-v3-lp-research-design.md`を前提とする。ExecPlan自体に実装に必要な契約を記載し、途中で再開しても別の会話を必要としないようにする。

## Progress

- [x] (2026-08-09) 既存リポジトリ、仕様DOCX、既存のweather研究設計、公式Base/Uniswap資料を確認した。
- [x] (2026-08-09) ユーザーがAIなしC0 v0.1から始める方針を承認した。
- [x] (2026-08-09) 日本語設計書と本ExecPlanの初版を作成した。
- [x] `base-lp-research/`のパッケージ、設定、fixture、テスト基盤を追加する。
- [x] RPCによるpool解決、event取得、正規化、品質検証、Parquet保存を追加する。receiptの長期履歴取得は保留。
- [x] tick/liquidity計算、state replay、portfolio相当のC0評価、P0 fee、Base cost modelを追加する。
- [x] baseline/threshold戦略、metrics、sweep、walk-forward判定、Markdown/CSV/Parquet reportを追加する。
- [x] unit/integration testと実データ1時間pilotを実行し、artifactを検査する。
- [x] `Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective`を実測結果で更新する。
- [ ] 9か月以上の実データpilot、receipt cost、P1/P2 fee precision、OOT fold結論を追加する。

## Surprises & Discoveries

- 観測: 既存リポジトリは初期コードがなく、`main`はweather研究設計のコミットを含む状態だった。影響: 既存ファイルを変更せず、研究を独立ディレクトリへ置く。
  証拠: `git status -sb`はclean、`git log`はweather設計コミットと初期コミットを示した。
- 観測: バンドルPythonは3.12.13で、web3、pyarrow、polars、duckdb、pytestは未導入だった。影響: 依存を`base-lp-research/pyproject.toml`に明示し、コアのfixture testは標準ライブラリだけでも検証できる境界にする。
  証拠: バンドルPythonのimport availability probeで、pandas/numpy/pydantic以外の主要研究依存が未導入だった。
- 観測: 仕様DOCXのLibreOfficeページレンダリングは、一時プロファイルのWindows権限と実行ファイル解決で完了しなかった。影響: 仕様読解はDOCX本文・表の構造抽出で行い、研究成果物にDOCXレンダリング依存を持ち込まない。
  証拠: `render_docx.py`は最初に`WinError 5`、昇格後に`WinError 2`で停止した。

## Decision Log

- 決定: v0.1はAI/RLなしのC0のみとする。理由: simulatorの正しさと会計をML/RLより先に検証する。日付/担当: 2026-08-09 / Codex。
- 決定: 第一実装は設定可能なblock/date rangeを持つpilotとする。理由: 3か月pilotを最初に取り、長期データが不足した場合に範囲を偽装しない。日付/担当: 2026-08-09 / Codex。
- 決定: Parquetを実データの受入形式とし、PyArrowなしではfixture testだけを許可する。理由: schema/checksum/partitionを維持し、CSVだけで研究完了と誤認しない。日付/担当: 2026-08-09 / Codex。
- 決定: Baseのhistorical costはreceiptのL2/L1情報を優先し、未取得部分は推定シナリオとして分離する。理由: BaseはL2 execution feeとL1 security feeの二部構成である。日付/担当: 2026-08-09 / Codex。
- 決定: pool addressは設定の固定値ではなくFactory `getPool(tokenA, tokenB, fee)`の結果をmanifestへ記録する。理由: 公式資料もchainごとにアドレスを確認するよう求めている。日付/担当: 2026-08-09 / Codex。

## Outcomes & Retrospective

1時間の実データsmokeで取得・decode・保存・検証・backtest・sweepの一連の再現性を確認した。仮説の成否を判断する長期pilotではなく、データ量不足、provider rate limit、receipt fee未取得、P0近似が残るため、ML/RLへの進行と投資判断は保留する。

## Context and Orientation

リポジトリルートは `C:\Users\Hodaka\Downloads\div\bot_research` であり、現在の本体は実質的に文書だけである。新しい研究は `base-lp-research/`に閉じ込める。Python packageのimport rootは `base-lp-research/src/base_lp/`、実データは `base-lp-research/data/`、設定は `base-lp-research/configs/`、テストは `base-lp-research/tests/`、結果は `base-lp-research/results/`とする。`data/`、`results/`、ローカル環境ファイルはGitignore対象にするが、schema、config example、small fixture、report templateは追跡する。

「event-driven」とは、一定間隔のローソク足へ集約せず、各Swapをblock number、transaction index、log indexの順に処理する方式である。「C0」とは、検証対象LPが過去のprice pathとpool liquidityを変えない小資本近似である。「point-in-time」とは、取引判断時点より後に公開された値を使わないという意味であり、strategy policyには処理済みevent timestampまでの入力しか渡さない。

データ取得は `BASE_RPC_URL` を環境変数から読み、未設定時は明示的にエラーにする。実行を再現するため、RPC URLの秘密部分は保存せず、provider名、chain ID、request range、取得時刻、response checksumだけをmanifestに保存する。Factory、NonfungiblePositionManager、WETH、USDCのアドレスは公式deployment資料とオンチェーンqueryで確認し、poolはFactoryから解決する。

## Plan of Work

### Milestone 1: 研究パッケージの境界とfixture

`base-lp-research/pyproject.toml`にPython 3.12以上、runtime依存の `httpx`、`pydantic`、`pyarrow`、`numpy`、`pandas`、`matplotlib`、test依存の`pytest`、`hypothesis`を明示する。ネットワークなしでもテストできるように、RPC clientはtransport interfaceを受け取る。`src/base_lp/schemas.py`に`BlockRef`、`LogRecord`、`SwapEvent`、`LiquidityEvent`、`PoolMetadata`、`DatasetManifest`、`BacktestConfig`を定義し、JSON fixtureから構築できるようにする。

`src/base_lp/data/rpc.py`は`JsonRpcClient.request(method: str, params: list[object]) -> object`と、`get_logs(address: str, topics: list[str], from_block: int, to_block: int) -> list[dict]`を提供する。429/5xx、JSON-RPC error、range too largeを指数バックオフとrange分割で処理するが、無限リトライはしない。`src/base_lp/data/events.py`はUniswap v3 PoolのSwap/Mint/Burn/Collect topicとABI dataをdecodeし、stable keyで並べる。

受入は、fixture RPCが返す1件のSwapを、同じJSONから同じcanonical representationへ変換し、duplicate keyを拒否するunit testが通ることである。実行場所はリポジトリルートとし、依存導入後に次を実行する。

    cd C:\Users\Hodaka\Downloads\div\bot_research\base-lp-research
    python -m pytest tests\unit\test_events.py -q

期待値は、指定fixtureのテストが全件passし、未来のtimestampやtopic mismatchが検出されることである。

### Milestone 2: pool解決とデータ取得

`src/base_lp/data/pool.py`に`resolve_pool(client, factory, token_a, token_b, fee) -> PoolMetadata`を作る。tokenアドレスはchecksum化し、Factoryへtoken順を比較しながらqueryする。pool addressがzero addressなら`pool_not_found`とする。`src/base_lp/data/ingest.py`に`collect_range(config) -> DatasetManifest`を作り、pool creationからpilot範囲のlogs、block timestamp、必要なreceiptを保存する。取得結果は`data/raw`と`data/normalized`へ分け、各partitionのschema versionとsha256をmanifestに記録する。

最初の実データ範囲は設定ファイルの `pilot_start_utc` と `pilot_end_utc` からblockへ解決する。時刻からblockへの解決はbinary searchで行い、同じtimestampを持つblockの境界をinclusive/exclusiveとしてmanifestへ残す。ログ取得はproviderの最大rangeに合わせて分割し、取得件数がゼロでも正常終了せず、pool未稼働か取得失敗かを区別する。

`src/base_lp/data/validate.py`はblock hashの連続性、log stable keyの一意性、event topic、token metadata、SwapのsqrtPriceX96/tick整合、取得範囲、checksumを検証する。欠損があればmanifest statusを`insufficient_data`または`collection_error`にし、backtest runnerは入力検証を通らないデータを受け付けない。

受入は、実RPCでpool addressをFactory queryしたmanifestが生成され、RPCが利用できない場合にゼロ行successではなく具体的なエラー状態を生成することである。実行例は次のとおりである。

    cd C:\Users\Hodaka\Downloads\div\bot_research\base-lp-research
    $env:BASE_RPC_URL = 'https://mainnet.base.org'
    python -m base_lp.cli resolve-pool --config configs/base_weth_usdc_005.yaml
    python -m base_lp.cli collect --config configs/base_weth_usdc_005.yaml
    python -m base_lp.cli validate-data --manifest results/dataset_manifest.json

### Milestone 3: v3 mathとC0 state replay

`src/base_lp/uniswap_v3/tick_math.py`に`get_sqrt_ratio_at_tick(tick: int) -> int`と`get_tick_at_sqrt_ratio(sqrt_price_x96: int) -> int`を整数演算で定義する。tick境界、最小/最大tick、rounding方向をUniswap v3-coreのgolden vectorで固定する。`src/base_lp/uniswap_v3/liquidity_math.py`に、価格とlower/upper sqrt priceからtoken0/token1数量を求める関数と、token数量からposition liquidityを求める関数を定義する。浮動小数点は表示用に限定し、state計算は整数を使う。

`src/base_lp/backtest/state.py`に`PoolState.replay(event: SwapEvent | LiquidityEvent) -> None`を作る。v0.1はeventのpost-stateを検証しながらtick、sqrt price、active liquidityを更新する。`src/base_lp/backtest/engine.py`は`run_backtest(events: Iterable[SwapEvent], strategy: Strategy, config: BacktestConfig) -> BacktestResult`を提供し、event以前のportfolio stateをstrategyへ渡し、action logへreset/no-action理由を記録する。

受入は、tick->sqrt->tickの境界テスト、価格が範囲内/下/上にある3ケース、同一fixtureの2回実行で同一hashのresultが得られること、policyが未来eventを参照できないことを確認することである。

    cd C:\Users\Hodaka\Downloads\div\bot_research\base-lp-research
    python -m pytest tests\unit\test_tick_math.py tests\unit\test_backtest_replay.py -q

### Milestone 4: portfolio、費用、baseline

`src/base_lp/backtest/portfolio.py`に`Portfolio.mark_to_market(price: Decimal) -> Decimal`、`Portfolio.apply_fee(...)`、`Portfolio.rebalance(...)`を定義する。`src/base_lp/backtest/cost_model.py`に`CostModel.estimate(action, block_fee, receipt_sample) -> CostBreakdown`を作る。L2 feeはgasUsed×effective gas priceで計算し、L1 feeはreceiptの履歴値があればそれを使い、なければ`estimated`フラグとp25/p50/p75シナリオを付ける。inventory swap feeとprice impactは入力size、fee tier、深さの近似から計算し、ゼロを暗黙に置かない。

`src/base_lp/strategies/`には、`HoldStrategy`、`FullRangeStrategy`、`StaticRangeStrategy`、`TauResetStrategy`、`ThresholdResetStrategy`を作る。全strategyは`decide(observation: Observation) -> Decision`を実装し、Decisionは`action`、`target_lower_tick`、`target_upper_tick`、`reason`、`expected_benefit`を持つ。Threshold Resetのactionは、`distance_to_center / half_width >= q`、cooldown、emergency volatility、expected benefit > cost+safety marginを満たしたときだけ`reset`になる。

`src/base_lp/backtest/benchmarks.py`はHODL value、LP value、HODL alpha、LVR diagnosticを別々に計算する。IL-likeをLVRと同じ式で二重控除しない。受入は、単一Swapのfee、単一resetのgas/swap cost、range外のno-fee、HODL比較のfixture計算が手計算期待値と一致することである。

### Milestone 5: metrics、sweep、walk-forward、report

`src/base_lp/metrics/performance.py`にnet return、HODL alpha、max drawdown、CVaR、Sharpe/Sortino/Calmar、time in range、fee capture、rebalances/day、gas/gross fee、swap cost/gross feeを定義する。`src/base_lp/experiments/runner.py`はwidth、q、cooldown、capital、gas scenarioのgridを再現可能な順序で走らせ、各runへconfig hashを付ける。`src/base_lp/experiments/walk_forward.py`はtrain 6か月、validation 1か月、test 2か月を基本にし、データが足りなければ`insufficient_data`とする。

`src/base_lp/reporting.py`は`results/backtest_summary.json`、`results/experiment_grid.csv`、`results/equity_curves.csv`、`results/figures/*.png`、`results/report.md`を作る。reportにはgross fees、gas+execution cost、HODL alphaのwaterfallと、Base/Ethereum比較がデータのある場合だけ含まれる。全run metadataへgit commit、dataset checksum、source block range、fee precision、counterfactual mode、latency、gas scenarioを保存する。

### Milestone 6: 実データpilotとDecision Gate

依存導入後、まずfixture test、次に短いRPC smoke test、最後に設定されたpilotを実行する。大きなログ取得を一度に行わず、block rangeを分割し、途中で停止してもmanifestを更新して再開できるようにする。pilotの取得後、2～5件の実在LP positionを選び、Collect/feeGrowthが取れる範囲でP0推定値と比較する。real LPの所有者や個人情報は結果へ不要に保存しない。

受入は、以下のDecision Gateを結果に明示することである。

G1: fee/state validationが目標誤差内か。未達なら戦略結論を出さずsimulatorを修正する。

G2: OOTのHODL-relative net alphaが複数foldでStatic Narrowを上回るか。pilot期間不足なら`insufficient_data`とする。

G3: gasシナリオとswap frictionを含めても優位性が残るか。残らなければ「Baseの低gasだけでは不十分」と記録する。

### Milestone 7: 仕上げと再現性確認

`README.md`に、依存導入、環境変数、pool resolution、collect、validate、backtest、reportの順を記載する。`.gitignore`にruntime dataとsecretを追加し、schema/config/fixture/test/report templateを追跡する。最後に`python -m pytest -q`、`python -m compileall src tests`、`python -m base_lp.cli --help`、fixture end-to-endを実行し、`git diff --check`と成果物の存在・JSON schema・CSV行数・checksumを検査する。

## Concrete Steps

すべてのコマンドは、特に記載がない限り `C:\Users\Hodaka\Downloads\div\bot_research` またはその下の `base-lp-research`で実行する。ファイルは小さく責務を分け、変更ごとに対象テストを実行する。実データ取得に失敗した場合は、エラーログを保持し、同じ入力で再試行できる範囲を確認してから次へ進む。

初期状態の確認:

    git status -sb
    git diff --stat

依存導入後の標準検証:

    cd C:\Users\Hodaka\Downloads\div\bot_research\base-lp-research
    python -m pytest -q
    python -m compileall src tests
    python -m base_lp.cli --help
    git diff --check

実データ実行時は`BASE_RPC_URL`を設定し、秘密情報をファイルやmanifestへ書き込まない。公開RPCのrate limitに達した場合は、より遅いrange分割、別provider、または公式indexer adapterへ切り替える。取得できない範囲は推測値で埋めず、manifest statusとreasonを更新する。

## Validation and Acceptance

コード受入は、全unit/golden/integration testがpassし、fixture end-to-endが同一checksumと同一resultを再生成すること。品質受入は、duplicate stable key、missing block、topic mismatch、future-data access、zero pool、zero eventを適切に拒否すること。研究受入は、実データpilotが`success`または理由付き`insufficient_data`で終わり、`results/dataset_manifest.json`と`results/backtest_summary.json`が存在し、各runにsource block range、dataset checksum、config、commit、費用仮定があること。

`success`は取得・検証・backtest・reportすべてが完了した状態に限る。`insufficient_data`は必要期間、RPC範囲、receipt fee、CEX reference、またはreal LP validationが足りず、結論を出せない状態とする。`collection_error`は通信、provider、JSON-RPC、decodeの実行失敗であり、`invalid_dataset`は取得したデータの整合性違反である。status名だけでなく、欠損期間、取得件数、除外件数、最初のエラー、再実行コマンドを保存する。

## Idempotence and Recovery

同じconfigと同じblock rangeでcollectを再実行しても、raw partitionはstable keyで重複排除され、manifest checksumが変わらないようにする。途中停止時は最後に確定したpartitionを残し、未取得rangeだけを再試行する。既存の`docs/superpowers/specs/2026-08-09-weather-polymarket-research-design.md`や、それに関係するユーザー変更は変更しない。生成データを消す必要がある場合は、`base-lp-research/data/`または`base-lp-research/results/`だけを対象にし、manifestとconfigは保持する。

## Artifacts and Notes

最終的に追跡する重要ファイルは `base-lp-research/pyproject.toml`、`configs/base_weth_usdc_005.yaml`、`src/base_lp/`、`tests/`、`README.md`、schema、fixtureである。通常Gitignoreするのはraw/normalized Parquet、large figures、local `.env`、cacheである。結果を共有する場合は、`results/dataset_manifest.json`、`results/backtest_summary.json`、`results/report.md`と、dataset checksumを一緒に保存する。

期待するreportの要点は次のような構造である。値を事前に埋めず、実行後にのみ記録する。

    status: success | insufficient_data | collection_error | invalid_dataset
    counterfactual_mode: C0
    fee_precision: P0
    primary_kpi: oot_hodl_relative_net_alpha
    data: source, chain_id, pool, block_range, row_counts, checksum
    cost: l2_execution, l1_security, inventory_swap, price_impact, slippage_mev_proxy
    strategies: HODL, FullRange, StaticNarrow, TauReset, ThresholdReset
    decision_gate: G1, G2, G3

## Interfaces and Dependencies

`base-lp-research/src/base_lp/schemas.py`は外部境界で使う型を定義する。`DatasetManifest`は`status: str`、`chain_id: int`、`pool: PoolMetadata`、`source: str`、`block_start: int`、`block_end: int`、`row_counts: dict[str, int]`、`checksums: dict[str, str]`、`errors: list[str]`を持つ。`BacktestConfig`は`counterfactual_mode: str`、`fee_precision: str`、`latency_blocks: int`、`capital_usd: Decimal`、`fee_tier: int`、`strategy_params: dict[str, object]`、`cost_scenario: str`を持つ。

`base-lp-research/src/base_lp/data/rpc.py`は標準JSON-RPC over HTTPを使う。`JsonRpcClient`はtransportを差し替え可能にし、fixture transportをunit testへ渡す。`data/events.py`はtopic hashとABI word offsetを固定し、`SwapEvent`へ`amount0`、`amount1`、`sqrt_price_x96`、`liquidity`、`tick`を入れる。`data/persist.py`はPyArrowがある場合にParquetを作り、なければfixture向けJSONLを作るが、実データ受入はParquetが必須である。

`uniswap_v3/tick_math.py`は整数の`get_sqrt_ratio_at_tick`、`get_tick_at_sqrt_ratio`、`uniswap_v3/liquidity_math.py`は整数のamount/liquidity変換を提供する。`backtest/engine.py`の`run_backtest(events, strategy, config)`はfuture eventを引数へ渡さず、`BacktestResult`へequity curve、fee ledger、cost ledger、action log、metrics inputを返す。`strategies/base.py`の`Strategy.decide(observation) -> Decision`はside effectを持たず、`Decision.action`は`hold`または`reset`に限定する。

依存の役割は、HTTP取得に`httpx`、入力と設定検証に`pydantic`、Parquetに`pyarrow`、集計に`pandas`/`numpy`、図に`matplotlib`、テストに`pytest`/`hypothesis`である。Uniswap v3の精密な整数演算のgolden vectorは公式v3-coreのTickMath/SqrtPriceMath/SwapMathに合わせ、ソースコードのライセンス条件を確認したうえで必要な定数・ロジックだけを実装する。

## 変更履歴

- 2026-08-09: 初版作成。既存仕様DOCXのv0.1要件、公式Base fee/Uniswap deployment方針、ユーザー承認済みのAIなしC0スコープをExecPlanへ反映した。
## 2026-08-09 実装進捗追記

### Progress

- [x] `base-lp-research/` の Python パッケージ、設定、fixture/unit test 基盤を作成した。
- [x] Swap/Mint/Burn/Collect の decode、canonical normalize、JSONL/Parquet persistence、checksum manifest を実装した。
- [x] Base RPC による pool 解決、block timestamp 推定、範囲分割 `eth_getLogs` 取得、実データ validation を実行した。
- [x] Tick math、liquidity math、C0 event-driven backtest、Static Narrow、Threshold Reset、cost model、HODL-relative metrics を実装した。
- [x] 3幅 × 3閾値の deterministic parameter sweep と、9か月未満を `insufficient_data` とする walk-forward gate を追加した。
- [x] `pytest` 40件、`compileall`、CLI help、実データ sweep を検証した。
- [ ] 9か月以上の実データ pilot、receipt の L2/L1 cost 履歴、P1/P2 fee precision、OOT fold 結論は未完了である。

### Surprises & Discoveries

- 公式 Base RPC では1時間の取得は成功したが、長期範囲は遅延・rate limit・payload 制限に当たりやすい。PublicNode は対象範囲の `eth_getLogs` が 403、HyperSync は token が必要だった。
- 実データの Collect event は recipient が non-indexed であり、topic 数4・data内 recipient/amount0/amount1 として decode する必要があった。
- 異なる event type を同じ Parquet に保存すると amount0/amount1 の型が混在するため、混在列を文字列へ正規化して大きな整数の欠損を防いだ。
- 2026-07-31 の1時間 smoke は 575 logs / 511 swaps で validation と backtest に成功したが、時間窓が短いため研究結論ではない。summary と report の両方で `research_status=insufficient_data` を保持する。

### Decision Log

- 実データ取得の再現性を優先し、RPC URL は `BASE_RPC_URL` からのみ読み込み、pool address は Factory `getPool` で解決する。
- まず C0/P0 を完成させ、real LP feeGrowth、exact SwapMath、receipt-derived gas/L1 fee を v0.1 の結論に混ぜない。これらは次の precision milestone とする。
- parameter sweep は config JSON の sorted canonical form を SHA-256 化し、行順ではなく `config_hash` で結果を追跡できるようにした。

### Outcomes & Retrospective

実装成果物は新規ディレクトリ `base-lp-research/` に隔離した。短時間の実データで一連の取得・decode・保存・検証・backtest・sweep の再現性を確認できた一方、長期結論を出すためのデータ量と履歴 cost evidence は不足している。したがって今回の decision gate は「研究基盤は通過、投資判断に関する G2/G3 は保留」とする。

## 2026-08-09 取得再開性・負荷制御の追記

- `collect` はデフォルト500 block、RPC timeout 20秒、retry 2回、request間隔0.25秒、1回90秒の実行上限とした。
- `results/collection_checkpoint.json` を chunk 完了ごとに更新し、同じ pool・block範囲・chunk設定で再実行した場合は `next_block` から再開する。中断とcheckpoint更新の境界で同じchunkを再取得しても stable key で重複追加しない。
- 取得途中は `dataset_manifest.status=partial` とし、backtestが未完成データを成功扱いしないようにした。全範囲完了後だけ Parquet と `status=success` を生成する。
- 同じUTC範囲のcheckpointを再利用する場合は、保存済み `block_start` / `block_end` を使い、timestamp→block解決のRPCを繰り返さない。古いcheckpointにはpartial manifestのUTC metadataをfallbackとして使う。
- 公式RPCに対する長期pilotはbounded runを開始し、複数回の実行で `next_block=45405627` まで進んだ。全範囲完了前なので、unit/integration testとcheckpointを併用して同じCLIを繰り返す運用とする。
