# Polymarket BTC 5m Collector and Lead-Lag 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Polymarket_5m_btc 内に、公開データを時刻安全に収集・検証し、Polymarket BTC 5m と外部 BTC 価格の lead-lag を分析できる、発注機能を持たない研究基盤を構築する。

**Architecture:** 各データ源の async adapter が共通の RawEvent に正規化し、受信直後の wall-clock / monotonic timestamp と raw payload を JSONL staging に追記する。検証済み staging を Parquet に compact し、coverage manifest と backward-only の研究処理から lead-lag レポートを生成する。Polymarket と Chainlink は公式 polymarket-client の AsyncPublicClient を使い、Binance・Coinbase・Hyperliquid は公開 WebSocket を使う。

**Tech Stack:** Python 3.13 実行環境（Python 3.12 互換範囲を宣言）、既存 .venv、pip、polymarket-client[quant]、websockets、httpx、orjson、pydantic、PyYAML、Typer、Rich、Polars、PyArrow、pytest、pytest-asyncio、ruff、pyright。

## Global Constraints

- 依存関係の導入、実行、テストには uv を使わず、.venv\Scripts\python.exe -m pip と .venv\Scripts\python.exe -m pytest を使う。
- 作業対象は Polymarket_5m_btc/ と、計画・設計文書および必要な Git 管理ファイルに限定する。
- 実注文、秘密鍵、ウォレット、認証済み trading client、資金移動を実装しない。
- 公開データを取得できない期間を合成データで埋めず、coverage と insufficient_data を出力する。
- 研究用の未来参照と、モデル特徴量作成時の未来参照を分離する。特徴量作成は local receive timestamp 以下の backward-only join に限定する。
- raw payload、source timestamp、local receive timestamp、local monotonic timestamp を削除しない。
- Windows PowerShell で再現できるコマンドを README に記録する。
- 既存の AGENTS.md、PLANS.md、既存変更を上書きしない。既存変更は stage 対象に含めない。
- Parquet が生成できるデータ量を超える大規模ダウンロードは、範囲・サイズを確認してから実行し、既定コマンドは無制限取得しない。
- 各実装単位は、先に対応する failing test を書き、失敗を確認してから production code を追加する。

## Progress

- [x] (2026-09-07) RESEARCH_PLAN.md を Polymarket_5m_btc/ に保存した。
- [x] (2026-09-07) collector-first / historical lead-lag の設計書を作成し、ユーザー承認を得た。
- [x] (2026-09-07) uv から pip への依存管理変更を計画へ反映した。
- [x] .venv を pip で利用可能にし、最小プロジェクトを install する。
- [x] 共通 event、staging、Parquet、quality 検証を実装する。
- [x] Polymarket / Chainlink / Binance / Coinbase / Hyperliquid の公開 collector を実装する。
- [x] PMXT coverage と Binance historical row normalization を実装する。大量のBinance archive downloadは明示的な次段階に残す。
- [x] receive-time lead-lag 分析、fixture report、README、CLI を完成させる。
- [x] default tests、fixture E2E、public smoke を検証した。Git pushのみ残る。
- [x] (2026-09-07) 段階Aとして、系列識別、Polymarket quote意味論、Binance bookTicker、horizon coverage、重複識別、収集manifest、rolling subscription、market master永続化を修復した。

## Surprises & Discoveries

- 観測: 現在の py.exe は Python 3.13.1 のみを提供し、既存 .venv は Python の分離には成功したが、ensurepip が一時ディレクトリの PermissionError で失敗した。
  証拠: .venv\Scripts\python.exe -c "import sys; print(sys.prefix != sys.base_prefix)" は True、-m pip は No module named pip。
- 観測: system Python 3.13 には pip 24.3.1、pytest 8.4.2、httpx、PyYAML があるが、polars、pyarrow、websockets、typer はない。
  証拠: system Python の import probe で polars / pyarrow / websockets / typer が false。
- 観測: 公式 PyPI の polymarket-client は Python >=3.11 を要求し、polymarket extra は polars / pyarrow を含む。公式 SDK は AsyncPublicClient、MarketSpec、CryptoPricesChainlinkTwapSpec(window_seconds=30|60) を公開している。
  証拠: Polymarket py-sdk の公開 pyproject と source、PyPI project metadata。
- 観測: PMXT v2 は polymarket_orderbook_YYYY-MM-DDTHH.parquet の UTC hourly archive で、空時間は file を作らない。
  証拠: PMXT v2 data overview。
- 観測: root に、過去の ensurepip 試行が残した権限付き .venv-bootstrap-tmp があり、git status が directory warning を出す。
  対応: 既知の作業用一時ディレクトリだけを権限付きで削除し、ユーザーの既存変更は触らない。
- 観測: 公式 SDK の `list_markets` は awaitable ではなく AsyncPaginator を即時返し、反復だけが非同期だった。
  対応: `await client.list_markets(...)` をやめ、終了時刻近傍のbounded filterでcatalog全件走査を避けた。
- 観測: Polymarket price-change の `hash` は単独ではイベントsequenceにならず、同じhashが複数rowに現れた。
  対応: source timestamp・asset・change index・local monotonic receiveを複合し、raw payloadのhashは保持した。
- 観測: 5秒smokeではChainlink更新がない場合がある。
  対応: collectorは `no_events` をmanifestに残し、別のbounded 10秒smokeで14行を確認した。欠測を0で埋めない。
- 観測: Binance Spot combined bookTickerの公式payloadは `u/s/b/B/a/A` で、tradeのような `e` は含まれない。
  対応: stream名とbookTickerの必須fieldで判定し、5秒smokeで `agg_trade=68`、`book_ticker=761` を確認した。
- 観測: Polymarketの5秒smokeではmarketイベント565行を保存できたが、Chainlink更新は無かった。
  対応: manifestを `partial` とし、Chainlink単独10秒smokeで30秒/60秒TWAPを各6行確認した。短時間の欠測を成功データとして扱わない。
- 観測: 同じPolymarket payloadを異なるlocal receiveで再受信すると、source sequenceは同じであるべきだが、受信IDは異なるべきである。
  対応: `sequence_id`からlocal monotonic値を外し、`receive_id`を別列として保存し、quality duplicate検査はchannel identityを含めた。

## Decision Log

- Decision: 初期保存は JSONL staging から Parquet compaction へ分ける。
  Rationale: WebSocket process crash で大きな Parquet writer を壊すリスクを避け、raw forensic source を残すため。
  Date/Author: 2026-09-07 / Codex
- Decision: Polymarket と Chainlink は公式 polymarket-client の async public client を使う。
  Rationale: 公式 SDK に market subscription と 30/60秒 Chainlink TWAP spec が存在し、ユーザー計画の unified SDK 方針と一致するため。
  Date/Author: 2026-09-07 / Codex
- Decision: 初期の外部 venue adapter は direct public WebSocket とする。
  Rationale: Binance、Coinbase、Hyperliquid の公開 feed から source timestamps と raw payload を直接保存でき、取引認証を導入せずに研究を始められるため。
  Date/Author: 2026-09-07 / Codex
- Decision: 依存管理は pip にする。
  Rationale: ユーザーの明示指定。requirements files と既存 .venv を使い、uv.lock は作らない。
  Date/Author: 2026-09-07 / Codex
- Decision: 初期成果物は lead-lag とデータ品質までにする。
  Rationale: fair probability、execution cost、paper/live trading は別の検証段階であり、最初から混ぜると alpha と execution bug を分離できないため。
  Date/Author: 2026-09-07 / Codex
- Decision: Polymarket market subscriptionは30秒ごとに再発見・再購読し、過去60秒以内に終了した市場を猶予付きで残す。
  Rationale: 5分市場の切替を取りこぼさず、SDK subscriptionの動的追加APIに依存せずに実装できるため。再購読時のreplay候補はstable source sequenceで検査する。
  Date/Author: 2026-09-07 / Codex
- Decision: collectorのmanifestは今回runで書いたpathとsource別件数だけを成功判定に使う。
  Rationale: 既存output-rootのファイルを見て空runを成功扱いすることを防ぎ、source欠落・再接続エラーを `partial` / `error` として残すため。
  Date/Author: 2026-09-07 / Codex

## Outcomes & Retrospective

fixture で staging JSONL → quality validation → ZSTD Parquet → exploratory lead-lag report を再現した。公開smokeでは、Binance 33、Coinbase 129、Hyperliquid 57、Polymarket 3,605、Chainlink 14行を保存できた。ただし段階Aレビューで、Binance bookTickerのparserが公式Spot payloadを取りこぼすこと、Polymarketの系列混在とprice_change意味論、受信ごとのsequence_id生成、長時間の市場切替未対応、既存ファイルを含むmanifest判定を確認した。したがって、以前の「品質検査は全てvalid」という記録は構造テストの証拠に限られ、実運用データ品質の証明とは扱わない。PMXTの3時間coverageは1時間present / 2時間missingで、ファイル本体は取得していない。実受信データの短時間lead-lagはexternal shock 0件のため `insufficient_data` であり、これはαやPnLの結果ではない。fair probability、execution simulator、paper/live traderはこの初期collector milestoneの範囲外である。
fixture で staging JSONL → quality validation → ZSTD Parquet → exploratory lead-lag report を再現した。公開smokeでは、Binance 33、Coinbase 129、Hyperliquid 57、Polymarket 3,605、Chainlink 14行を保存できた。ただし段階Aレビューで、Binance bookTickerのparserが公式Spot payloadを取りこぼすこと、Polymarketの系列混在とprice_change意味論、受信ごとのsequence_id生成、長時間の市場切替未対応、既存ファイルを含むmanifest判定を確認した。したがって、以前の「品質検査は全てvalid」という記録は構造テストの証拠に限られ、実運用データ品質の証明とは扱わない。段階Aでは、現行run単位のmanifest、market-aware lead-lag、horizon coverage、stable sequence / receive id、rolling subscription、restart-safe market masterを追加した。Binance 5秒smokeは829行（agg_trade 68 / book_ticker 761）、Polymarket 5秒smokeは565行でChainlink欠測のためpartial、Chainlink 10秒smokeは30s/60s各6行だった。PMXTの3時間coverageは1時間present / 2時間missingで、ファイル本体は取得していない。実受信データの短時間lead-lagはexternal shock 0件のため `insufficient_data` であり、これはαやPnLの結果ではない。fair probability、execution simulator、paper/live traderはこの初期collector milestoneの範囲外である。

## Context and Orientation

リポジトリ root は C:\Users\Hodaka\Downloads\div\bot_research であり、今回のプロジェクト root は Polymarket_5m_btc/ である。既に研究計画 Polymarket_5m_btc/RESEARCH_PLAN.md と設計書 docs/superpowers/specs/2026-09-07-polymarket-btc5m-collector-lead-lag-design.md が存在する。

「raw event」は、ある外部サービスから一度受信した一件のメッセージを意味する。「source timestamp」はサービスがメッセージに付けた時刻、「local receive timestamp」はこの PC が受信した時刻である。「staging」は collector が追記する再処理前の保存場所、「compaction」は staging の行を Parquet にまとめる処理である。「backward-only」は、決定時刻以前に受信済みの値だけを結合する時系列処理である。

初期のファイル構成は次のとおりにする。

- Polymarket_5m_btc/pyproject.toml: installable package metadata と btc5m CLI entry point。
- Polymarket_5m_btc/requirements.txt: 実行依存関係。
- Polymarket_5m_btc/requirements-dev.txt: -r requirements.txt と開発依存関係。
- Polymarket_5m_btc/.gitignore: .venv、data、cache、secrets、.pytest_cache を除外。
- Polymarket_5m_btc/src/btc5m/__init__.py: package version。
- Polymarket_5m_btc/src/btc5m/__main__.py: python -m btc5m。
- Polymarket_5m_btc/src/btc5m/cli.py: Typer command。
- Polymarket_5m_btc/src/btc5m/events.py: RawEvent と source timestamp 正規化。
- Polymarket_5m_btc/src/btc5m/clock.py: wall-clock / monotonic receive timestamp。
- Polymarket_5m_btc/src/btc5m/storage.py: staging append、partition path、Parquet compaction。
- Polymarket_5m_btc/src/btc5m/quality.py: event / book 品質検査。
- Polymarket_5m_btc/src/btc5m/config.py: YAML config と defaults。
- Polymarket_5m_btc/src/btc5m/collectors/*.py: source adapters。
- Polymarket_5m_btc/src/btc5m/ingestion/*.py: historical adapters。
- Polymarket_5m_btc/src/btc5m/research/lead_lag.py: event study と coverage report。
- Polymarket_5m_btc/tests/: unit と fixture E2E。
- Polymarket_5m_btc/config/research.yaml: bounded research defaults。
- Polymarket_5m_btc/config/live.yaml: collector defaults。発注設定は置かない。
- Polymarket_5m_btc/reports/: generated markdown/json reports。
- Polymarket_5m_btc/data/: raw/staging/normalized/features/datasets。

## Plan of Work

### Milestone 1: pip bootstrap and installable skeleton

この milestone では、既存 .venv を pip で使える状態にし、empty package を install して python -m btc5m --help が動くところまで作る。既存 .venv を作り直す前に ensurepip failure の原因を避けるため、仮想環境の site-packages に system pip から pip を導入するか、同じ target で公式 pip bootstrap を使う。結果は .venv\Scripts\python.exe -m pip --version で検証する。

必要なファイルは pyproject.toml、requirements.txt、requirements-dev.txt、.gitignore、package skeleton、README、最小 CLI、tests/test_cli.py である。ここでは network collector を起動しない。

### Milestone 2: canonical event, storage, and quality

canonical event の failing tests を先に作成する。RawEvent.from_message は source と event_type を必須とし、wall-clock ISO UTC、epoch milliseconds、integer nanoseconds の入力を内部 epoch microseconds に正規化する。欠落 source time は null を許し、local receive time は collector boundary で必須にする。

storage.append_jsonl は parent partition を作成し、JSON object 一行を append する。partition_path(root, source, receive_ts) は data/raw_staging/<source>/date=YYYY-MM-DD/hour=HH/events.jsonl を返す。compact_jsonl_to_parquet は stable canonical columns を持つ Parquet を ZSTD で書き、source/date/hour を追加列として保存する。

quality.validate_events は timestamp inversion、同じ source + sequence_id の重複、negative spread、crossed book、missing local receive timestamp を検出し、件数と sample error を JSON serializable な result にする。

### Milestone 3: public source adapters

まず parser の failing tests を各 source の representative payload で作る。parser は network connection を持たず、payload と ReceiveStamp から RawEvent の list を返す。

Polymarket parser は SDK の MarketEvent を JSON-compatible mapping にして book、price_change、last_trade_price、best_bid_ask、tick_size_change、market_resolved を保存する。market discovery は public client の market pagination から BTC / Up or Down / 5m だけを残し、slug/title から window start/end を検証する。subscription は MarketSpec(token_ids=..., custom_feature_enabled=True) を使う。

Chainlink parser は CryptoPricesChainlinkTwapSpec(window_seconds=30|60, symbols=["btc/usd"]) の 2 subscription を作り、payload timestamp、outer event timestamp、local receive timestamp を別々に保存する。

Binance parser は BTCUSDT@aggTrade と BTCUSDT@bookTicker の payload を区別する。Coinbase parser は BTC-USD ticker / market_trades subscription を作る。Hyperliquid parser は BTC trades / bbo subscription を作る。全 adapter は shared reconnect runner を使い、接続終了・decode error・server error を manifest に記録し、bounded exponential backoff で再接続する。

collector command は --sources、--duration-seconds、--output-root、--once を持つ。--duration-seconds 0 は無制限ではなく validation error とする。発注 client や secret env は import しない。

### Milestone 4: historical ingestion and coverage

PMXT adapter の failing tests を、HTML index に存在する hour、空の hour、既存 manifest の三例で作る。inspect_pmxt_coverage(start, end, index_html) は requested hours、present hours、missing hours、direct URLs を返す。archive は https://r2v2.pmxt.dev/polymarket_orderbook_YYYY-MM-DDTHH.parquet の pattern を使い、存在確認なしに巨大ファイルを取得しない。

download_pmxt_hours は一時ファイルへ stream してから atomic rename し、manifest の URL / size / sha256 を使って既存 validated file を skip する。missing hour は error ではなく coverage report の欠損になる。

Binance historical adapter は指定日範囲を daily aggTrades URL に変換し、zip 内 CSV の timestamp unit を検出して canonical UTC microseconds に変換する。CSV parse error と HTTP 404 は該当日を missing として記録し、他の日を止めない。既定実行は manifest / coverage のみで、download は明示 flag が必要である。

### Milestone 5: receive-time-safe lead-lag research

lead-lag の failing tests は、外部 quote の jump と、その後の Polymarket midpoint response が既知の小 fixture で確認できるようにする。detect_shocks(events, threshold_bps) は source event timestamp ではなく local receive timestamp の順序で shock を作る。measure_response(shocks, polymarket_events, horizons_ms) は各 horizon の将来観測を測るが、これは評価用の forward lookup であり、feature generation ではない。

build_external_median は同一 receive timestamp bucket の Binance / Coinbase / Hyperliquid mid の中央値を返す。欠損 source を 0 や前値で埋めず、設定した最小 source 数を満たさない bucket を excluded とする。

run_lead_lag_report は source counts、shock count、horizon 別 sample / mean / median / missing、event-time と receive-time の別を含む JSON と Markdown を書く。観測件数が minimum_samples 未満なら result_state=insufficient_data とし、PnL や alpha の成功とは報告しない。Polymarket historical archive に Chainlink path がない場合は result_state に historical_proxy を付与する。

### Milestone 6: end-to-end fixture, documentation, and validation

one-shot fixture command で canonical events を staging に書き、quality JSON、Parquet、lead-lag report を生成する。README.md には PowerShell での pip install、test、fixture、public collector、coverage-only ingestion の順に記録する。実データ download の既定範囲は bounded にし、全期間取得コマンドは提示するが自動実行しない。

全テスト、ruff、pyright の順で確認し、実行した Python path と dependency versions を reports/01_data_quality.md に記録する。最後に対象ファイルだけを stage して commit し、git show --stat と git status を確認してから git push origin main を行い、remote main の SHA を再取得して push 先を確認する。

## Concrete Steps

### Task 1: pip bootstrap and package skeleton

Files:

- Create: Polymarket_5m_btc/pyproject.toml
- Create: Polymarket_5m_btc/requirements.txt
- Create: Polymarket_5m_btc/requirements-dev.txt
- Create: Polymarket_5m_btc/.gitignore
- Create: Polymarket_5m_btc/src/btc5m/__init__.py
- Create: Polymarket_5m_btc/src/btc5m/__main__.py
- Create: Polymarket_5m_btc/src/btc5m/cli.py
- Create: Polymarket_5m_btc/README.md
- Test: Polymarket_5m_btc/tests/test_cli.py

Interfaces:

- Produces btc5m.cli:app and python -m btc5m --help.
- Produces requirements installed by .venv\Scripts\python.exe -m pip install -r requirements-dev.txt.
- Does not import any secret or trading module.

- [x] Step 1: Write test_cli_help_mentions_collect_and_fixture and assert subprocess help output contains the research commands.
- [x] Step 2: Run the focused test first and observe the absent-package failure.
- [x] Step 3: Add package metadata, requirements, cli.py, and __main__.py without network imports.
- [x] Step 4: Bootstrap only the target `.venv` with pip after ensurepip hit the host Temp ACL issue.
- [x] Step 5: Install requirements-dev.txt and the editable package with pip.
- [x] Step 6: Confirm the focused test and record Python 3.13.1 / polymarket-client 0.9.0.
- [x] Step 7: Add README.md/.gitignore and verify CLI help, including lead-lag and event-study aliases.

### Task 2: canonical events and storage

Files:

- Create: Polymarket_5m_btc/src/btc5m/events.py
- Create: Polymarket_5m_btc/src/btc5m/clock.py
- Create: Polymarket_5m_btc/src/btc5m/storage.py
- Create: Polymarket_5m_btc/src/btc5m/quality.py
- Test: Polymarket_5m_btc/tests/test_events_storage.py
- Test: Polymarket_5m_btc/tests/test_quality.py

Interfaces:

- ReceiveStamp = NamedTuple with local_receive_ts: datetime and local_monotonic_ns: int.
- RawEvent.from_message(source, symbol, event_type, payload, received, source_event_ts, source_publish_ts, sequence_id, price, bid, ask, bid_size, ask_size) -> RawEvent.
- RawEvent.to_row() -> dict[str, object].
- partition_path(root: Path, source: str, received: datetime) -> Path.
- append_jsonl(path: Path, row: Mapping[str, object]) -> None.
- compact_jsonl_to_parquet(input_path: Path, output_path: Path) -> int.
- validate_events(rows: Iterable[Mapping[str, object]]) -> QualityResult.

- [x] Step 1: Write tests for timestamp normalization, raw payload JSON preservation, partition path, append/reload, and empty compaction.
- [x] Step 2: Run focused tests first and observe the missing-module failures.
- [x] Step 3: Implement the dataclass, timestamp parser, JSONL writer, and partition helper.
- [x] Step 4: Run focused tests and confirm JSONL/timestamp assertions.
- [x] Step 5: Add Polars Parquet compaction with ZSTD and canonical columns.
- [x] Step 6: Add missing receive, duplicate, inversion, negative quote, and crossed-book checks.
- [x] Step 7: Run the focused event/storage/quality tests successfully.

### Task 3: source payload parsers and reconnect runner

Files:

- Create: Polymarket_5m_btc/src/btc5m/collectors/common.py
- Create: Polymarket_5m_btc/src/btc5m/collectors/polymarket.py
- Create: Polymarket_5m_btc/src/btc5m/collectors/chainlink.py
- Create: Polymarket_5m_btc/src/btc5m/collectors/binance.py
- Create: Polymarket_5m_btc/src/btc5m/collectors/coinbase.py
- Create: Polymarket_5m_btc/src/btc5m/collectors/hyperliquid.py
- Create: Polymarket_5m_btc/src/btc5m/config.py
- Test: Polymarket_5m_btc/tests/test_collectors.py

Interfaces:

- parse_binance_message(payload, received) -> list[RawEvent].
- parse_coinbase_message(payload, received) -> list[RawEvent].
- parse_hyperliquid_message(payload, received) -> list[RawEvent].
- parse_polymarket_message(payload, received) -> list[RawEvent].
- parse_chainlink_message(payload, received, window_seconds) -> list[RawEvent].
- reconnect_forever(source, connect, sink, stop, reconnect_base_seconds=1.0, reconnect_max_seconds=30.0) -> None.
- discover_btc_5m_markets(client: AsyncPublicClient) -> list[MarketIdentity].

- [x] Step 1: Add representative fixtures from official payload shapes and tests for all five adapters.
- [x] Step 2: Run parser tests first and observe the import failures.
- [x] Step 3: Implement pure parsers without opening sockets and retain raw payloads.
- [x] Step 4: Run parser tests and confirm all source fixtures normalize.
- [x] Step 5: Implement bounded reconnect behavior and its retry test.
- [x] Step 6: Implement SDK discovery/subscriptions and bounded end-date filtering for live discovery.
- [x] Step 7: Run 5-second/10-second opt-in public smoke; default tests remain offline.

### Task 4: historical ingestion and coverage

Files:

- Create: Polymarket_5m_btc/src/btc5m/ingestion/__init__.py
- Create: Polymarket_5m_btc/src/btc5m/ingestion/pmxt.py
- Create: Polymarket_5m_btc/src/btc5m/ingestion/binance_history.py
- Test: Polymarket_5m_btc/tests/test_ingestion.py

Interfaces:

- requested_utc_hours(start, end) -> list[datetime].
- inspect_pmxt_coverage(start, end, index_html) -> CoverageManifest.
- pmxt_url(hour) -> str.
- download_pmxt_hours(manifest, output_dir, client, allow_download) -> DownloadManifest.
- binance_daily_url(symbol, day) -> str.
- normalize_aggtrade_rows(csv_rows) -> list[RawEvent].
- build_binance_coverage(start, end) -> CoverageManifest.

- [x] Step 1: Add fixture tests for PMXT index parsing, missing hours, idempotent existing file, and HTTP 404 as missing.
- [x] Step 2: Run focused ingestion tests first and observe the missing-module failure.
- [x] Step 3: Implement regex coverage parsing and strict UTC hour boundaries.
- [x] Step 4: Implement atomic `.part` download, SHA-256 manifest, and existing-file skip behavior.
- [x] Step 5: Implement Binance daily URL, CSV normalization, and explicit millisecond/microsecond handling.
- [x] Step 6: Run focused tests and bounded PMXT coverage-only against the public index; no large archive download.

### Task 5: receive-time-safe lead-lag research

Files:

- Create: Polymarket_5m_btc/src/btc5m/research/__init__.py
- Create: Polymarket_5m_btc/src/btc5m/research/lead_lag.py
- Modify: Polymarket_5m_btc/src/btc5m/cli.py
- Test: Polymarket_5m_btc/tests/test_lead_lag.py

Interfaces:

- detect_shocks(events, threshold_bps) -> list[ShockEvent].
- build_external_median(events_by_source, bucket_ms, minimum_sources) -> list[RawEvent].
- measure_response(shocks, polymarket_events, horizons_ms) -> list[ResponsePoint].
- run_lead_lag_report(external_path, polymarket_path, output_path, threshold_bps, horizons_ms, minimum_samples) -> LeadLagReport.
- LeadLagReport.result_state is one of insufficient_data, exploratory, ready_for_next_gate.

- [x] Step 1: Write fixture tests with a known external jump and delayed Polymarket response.
- [x] Step 2: Run focused tests and observe failures.
- [x] Step 3: Implement shock detection using local receive timestamp.
- [ ] Step 4: Implement multi-venue median / minimum-source consensus as a follow-up feature milestone.
- [x] Step 5: Implement bounded future response measurement for evaluation and keep it separate from feature construction.
- [x] Step 6: Implement JSON report output with explicit result state.
- [x] Step 7: Run focused tests and the report on fixture and bounded smoke Parquet files.

### Task 6: CLI integration, README, E2E, and validation

Files:

- Modify: Polymarket_5m_btc/src/btc5m/cli.py
- Modify: Polymarket_5m_btc/README.md
- Create: Polymarket_5m_btc/config/research.yaml
- Create: Polymarket_5m_btc/config/live.yaml
- Create: Polymarket_5m_btc/tests/test_e2e_fixture.py
- Create: Polymarket_5m_btc/reports/01_data_quality.md

Interfaces:

- btc5m fixture --output-root PATH writes staging JSONL, compact Parquet, quality JSON, and a lead-lag report.
- btc5m collect --sources SOURCE,... --duration-seconds N starts only requested public collectors.
- btc5m compact --input PATH --output PATH compacts one staging file.
- btc5m validate --input PATH --output PATH writes quality JSON.
- btc5m ingest-pmxt --start ISO --end ISO [--download] writes coverage and optionally bounded downloads.
- btc5m event-study --external PATH --polymarket PATH --output PATH writes lead-lag report.

- [x] Step 1: Write the CLI fixture E2E test and assert artifact existence plus exploratory result state.
- [x] Step 2: Run the E2E test before wiring and observe its failure.
- [x] Step 3: Wire commands and enforce duration/source validation.
- [x] Step 4: Run the E2E test and inspect JSON/Markdown outputs.
- [x] Step 5: Document pip setup, fixture, coverage-only ingestion, and public collectors.
- [x] Step 6: Run the full test suite, ruff, pyright, and git diff --check.
- [x] Step 7: Update this plan's Progress, Surprises, Decision Log, and Outcomes with evidence.

### Task 7: commit and push to main

Files:

- Modify: .agent/2026-09-07-polymarket-btc5m-collector.md
- Stage only files created or modified by this project; do not stage existing unrelated changes.

- [x] (2026-09-07) Step 1: Run the initial collector milestone verification and push it to `main`.
- [x] (2026-09-07) Step 2: Confirm the initial push target and remote SHA before starting Stage A.
- [ ] Step 3: Commit the Stage A changes with a Conventional Commit subject.
- [ ] Step 4: Push Stage A explicitly with `git push origin main`.
- [ ] Step 5: Run `git ls-remote origin refs/heads/main` and confirm it matches the Stage A commit.
- [ ] Step 6: Re-run `git status --short --branch` and report the final worktree state.

### Task 8: 段階Aの計測・分析正確性修復

Files:

- Modify: Polymarket_5m_btc/src/btc5m/events.py
- Modify: Polymarket_5m_btc/src/btc5m/storage.py
- Modify: Polymarket_5m_btc/src/btc5m/quality.py
- Modify: Polymarket_5m_btc/src/btc5m/collectors/binance.py
- Modify: Polymarket_5m_btc/src/btc5m/collectors/polymarket.py
- Modify: Polymarket_5m_btc/src/btc5m/collectors/common.py
- Modify: Polymarket_5m_btc/src/btc5m/collectors/live.py
- Modify: Polymarket_5m_btc/src/btc5m/market_master.py
- Modify: Polymarket_5m_btc/src/btc5m/research/lead_lag.py
- Modify: Polymarket_5m_btc/tests/test_events_storage.py
- Modify: Polymarket_5m_btc/tests/test_quality.py
- Modify: Polymarket_5m_btc/tests/test_collectors.py
- Modify: Polymarket_5m_btc/tests/test_live.py
- Modify: Polymarket_5m_btc/tests/test_lead_lag.py
- Create: Polymarket_5m_btc/tests/test_market_master.py

The canonical event adds a stable source sequence and a distinct local receive id; the latter changes on replay and must never be used as a source duplicate key. Polymarket events retain market identity and token identity, while lead-lag groups observations by market/token series. `price_change` keeps its changed level only in the raw payload and contributes to a probability series only when its best bid and ask are present. Binance recognizes both raw and combined official bookTicker payloads, whose schema has no `e` event field.

The live collector refreshes market discovery on a bounded interval, preserves previously discovered market rows by upsert, and reports per-source counts and errors from the current run only. An empty or errored source is not reported as `ok`. Event-study horizons require observations through the requested deadline; an observation from the tail of a truncated file is not reused for a later horizon. The quality validator marks empty input invalid.

- [x] (2026-09-07) Step 1: Add failing tests for official Binance bookTicker parsing, stable-vs-receive IDs, market/token-separated lead-lag, deep price-change exclusion, horizon truncation, empty quality, market-master upsert, and rolling token selection.
- [x] (2026-09-07) Step 2: Run the focused tests and confirm each failure was caused by the current behavior.
- [x] (2026-09-07) Step 3: Add market identity and receive-id fields, stable Polymarket source IDs, and Binance payload routing.
- [x] (2026-09-07) Step 4: Make lead-lag series market-aware and horizon-coverage-aware; return null means when no sample exists.
- [x] (2026-09-07) Step 5: Add per-run collector counters/errors, rolling Polymarket discovery, and restart-safe market-master merge.
- [x] (2026-09-07) Step 6: Run focused tests, then the full suite, ruff, pyright, fixture, public smoke, and diff checks.
- [ ] Step 7: Commit the Stage A changes, push `main`, and verify the remote SHA.

## Validation and Acceptance

From Polymarket_5m_btc/, the following commands must work after installation:

    .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    .venv\Scripts\python.exe -m pip install -e .
    .venv\Scripts\python.exe -m pytest -q
    .venv\Scripts\python.exe -m btc5m fixture --output-root .\data\fixture-run
    .venv\Scripts\python.exe -m btc5m --help

Expected behavior is a zero exit code for installation, tests, fixture, and help. The fixture command must create staging JSONL, Parquet, quality JSON, and a Markdown/JSON lead-lag result. The fixture report must say insufficient_data or exploratory and must not imply profitable trading.

Repository-root verification is:

    git diff --check
    git status --short --branch
    git show --stat --oneline HEAD

Before push, tests and static checks must have fresh successful output. After push, git ls-remote origin refs/heads/main must print the same commit SHA as local git rev-parse HEAD.

## Idempotence and Recovery

Running install commands repeatedly is safe. JSONL append is intentionally not idempotent; a collector restart can create duplicate events, which the quality manifest reports. Parquet compaction writes to a temporary output and atomically replaces only the requested output path. Historical downloads skip validated files and never overwrite them.

If a focused test fails, stop at that task, add or correct a failing regression test, and rerun the smallest test command before continuing. If public network access fails, do not replace it with fake data; complete offline parser/fixture tests and report the live limitation. If pip installation fails inside .venv, do not modify system Python packages; repair only the target environment or report the blocked dependency.

The known root .venv-bootstrap-tmp is not part of the project. Remove only that exact directory after verifying its absolute path; do not use a broad recursive delete.

## Artifacts and Notes

At the end of the first milestone, the important artifacts are:

    Polymarket_5m_btc/data/raw_staging/<source>/date=YYYY-MM-DD/hour=HH/events.jsonl
    Polymarket_5m_btc/data/raw/<source>/date=YYYY-MM-DD/hour=HH/events.parquet
    Polymarket_5m_btc/data/manifests/coverage.json
    Polymarket_5m_btc/reports/01_data_quality.md
    Polymarket_5m_btc/reports/03_lead_lag.md

A typical local fixture evidence is:

    result_state: insufficient_data
    external_events: 3
    polymarket_events: 3
    horizons_ms: [100, 250, 500, 1000]

This is a research artifact state, not a trading result.

## Interfaces and Dependencies

The project uses the official PyPI distribution polymarket-client and imports polymarket. The public collector uses AsyncPublicClient, MarketSpec, and CryptoPricesChainlinkTwapSpec. The current official SDK exposes market event subscriptions through an async handle; no synchronous wrapper is added.

Runtime dependencies are installed by:

    .venv\Scripts\python.exe -m pip install -r requirements.txt

The requirements file contains:

    polymarket-client[quant]>=0.9,<0.10
    websockets>=13,<16
    httpx>=0.27,<1
    orjson>=3.10,<4
    pydantic>=2,<3
    PyYAML>=6,<7
    typer>=0.12,<1
    rich>=13,<14

The development requirements file includes the runtime file plus:

    pytest>=8,<9
    pytest-asyncio>=0.23,<1
    hypothesis>=6,<7
    ruff>=0.11,<1
    pyright>=1.1,<2

The source adapter transport rules are explicit:

- Polymarket and Chainlink: official SDK async subscriptions.
- Binance: public wss://stream.binance.com:9443/ws streams.
- Coinbase: public wss://advanced-trade-ws.coinbase.com feed.
- Hyperliquid: public wss://api.hyperliquid.xyz/ws feed.
- PMXT: public HTTPS hourly Parquet objects.
- Binance historical: public data archive HTTPS objects.

No runtime dependency reads wallet keys or submits orders.

## Note on this revision

2026-09-07: Updated dependency management from uv to pip at the user's request. Replaced uv.lock / uv run instructions with .venv\Scripts\python.exe -m pip and .venv\Scripts\python.exe -m pytest, and added the decision to the log.

2026-09-07: Implemented the collector-first milestone, added bounded PMXT coverage inspection, explicit historical receive-time nulls, fixture/CLI reports, market master output, collection manifests, and public smoke evidence. The remaining gate before completion is the fresh full verification plus main push.
