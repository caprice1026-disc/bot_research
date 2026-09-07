# Polymarket BTC 5m Collector and Historical Lead-Lag Design

Date: 2026-09-07

## Purpose

This first implementation milestone makes the research question measurable without placing orders: it captures the arrival times and market events needed to compare external BTC price innovations with Polymarket BTC Up/Down 5m prices, then produces an honest lead-lag report that distinguishes statistical evidence from data insufficiency.

The project remains isolated under `Polymarket_5m_btc/`. The full research plan is preserved in `Polymarket_5m_btc/RESEARCH_PLAN.md`.

## Scope

In scope:

- A self-contained Python project with configuration, CLI, tests, and data directories.
- A canonical raw-event schema containing source time, publish time, local receive time, monotonic receive time, sequence information, normalized quote fields, and raw payload.
- Crash-safe prospective capture for Polymarket, Chainlink 30s/60s TWAP, Binance, Coinbase, and Hyperliquid, subject to each public interface being available and verified.
- Market discovery for BTC Up/Down 5m markets, including explicit window start/end and token IDs.
- Historical ingestion adapters for the available pmxt Polymarket archive and Binance public aggTrades, with coverage manifests and no synthetic gap filling.
- A minimal point-in-time lead-lag analysis using receive-time-safe joins.
- Data-quality checks and reports with an explicit `insufficient_data` state.

Out of scope for this milestone:

- Private keys, authenticated trading, order placement, or real-money execution.
- Claims that historical Polymarket data exactly represents the Chainlink 60s resolver path.
- Machine-learning model selection, threshold optimization, or live paper PnL.
- Kafka, Redis, PostgreSQL, or a distributed service.

## Design decisions

### Project boundary

All new code, configuration, tests, data, and reports live below `Polymarket_5m_btc/`. Existing research packages in the repository are not modified. The existing repository-level instructions and validation conventions still apply.

### Runtime

The requested target is Python 3.12. The current host exposes Python 3.13 only. The project will declare a compatible Python range and record the actual interpreter in manifests. Dependency installation uses the existing `.venv` and pip, with `requirements.txt` and `requirements-dev.txt`; implementation must not claim Python 3.12-specific validation when it ran under 3.13.

### Raw capture format

Collectors write append-only JSON Lines batches to a staging partition first. Each line is one canonical event plus the original payload. This keeps a reconnect or process crash from invalidating a large Parquet file. A compaction command converts validated staging data to ZSTD-compressed Parquet partitions under `data/raw/`. Research code consumes Parquet, while the staging files remain the forensic source.

### Time semantics

Every event records both source timestamps and local timestamps. `local_receive_ts` is captured immediately after the message is received, and `local_monotonic_ns` is used for local ordering and latency measurements. Feature builders only use events whose local receive time is less than or equal to the decision time. Nearest/future joins are not permitted.

### Public-only safety

The first milestone uses public market data only. Credentials are not required. The collector has no trading client, wallet, order, or funds-transfer path. Any later live execution work requires a separate explicit scope decision.

## Architecture

The data flow is:

1. A source adapter receives a message.
2. The adapter captures wall-clock and monotonic local receive timestamps immediately.
3. The adapter normalizes the source message into a canonical event while retaining the raw payload.
4. The partition writer appends the event to a date/hour/source staging file.
5. A validator checks timestamps, duplicate keys, sequence gaps where available, and market-book invariants.
6. A compactor writes validated Parquet.
7. The historical/feature layer performs backward-only point-in-time joins.
8. The lead-lag analyzer emits coverage, sample counts, response curves, and an explicit result state.

The source adapters are isolated so that an SDK API change affects only the adapter, not storage, validation, or research.

## Planned modules

The first implementation plan will create the following focused modules under `Polymarket_5m_btc/src/btc5m/`:

- `events.py`: canonical event model and serialization.
- `clock.py`: wall-clock and monotonic receive timestamp capture.
- `storage.py`: staging partition paths, append-only writes, and Parquet compaction.
- `quality.py`: duplicate, timestamp, sequence, spread, crossed-book, and freshness checks.
- `config.py`: typed configuration loaded from YAML/environment without secrets.
- `collectors/polymarket.py`: market discovery and public CLOB stream adapter.
- `collectors/chainlink.py`: RTDS Chainlink 30s/60s adapter.
- `collectors/binance.py`: public aggTrade/bookTicker live adapter.
- `collectors/coinbase.py`: public Advanced Trade ticker/market-trades adapter.
- `collectors/hyperliquid.py`: public trades/BBO adapter.
- `ingestion/pmxt.py`: archive coverage inspection and idempotent download.
- `ingestion/binance_history.py`: public aggTrade import with timestamp-unit detection.
- `research/lead_lag.py`: receive-time-safe event study and predictive response summaries.
- `cli.py`: commands for collect, compact, validate, ingest, and lead-lag reporting.

The implementation may combine very small helpers when that reduces duplication, but each source adapter must retain a clear boundary.

## Canonical event contract

Every stored event must include:

- `source`
- `symbol`
- `event_type`
- `source_event_ts`
- `source_publish_ts`
- `local_receive_ts`
- `local_monotonic_ns`
- `sequence_id`
- `price`
- `bid`
- `ask`
- `bid_size`
- `ask_size`
- `raw_payload`

Fields unavailable from a source remain null; they are never fabricated. The source adapter may add source-specific metadata under a namespaced metadata object, but the canonical fields above remain stable.

## Market identity and resolver limits

Market discovery records slug, condition ID, UP/DOWN token IDs, title/window timestamps, market creation time, fee metadata when public, and the source URL or endpoint used. The parser must verify that the market is BTC, Up/Down, and 5m before subscription.

Historical CLOB data without an exact Chainlink observation path is labeled `historical_proxy`. Only prospective data containing the self-captured Chainlink stream can be labeled `resolver_observed`. Reports must not call the former resolver-aware evidence.

## Error handling and recovery

Collectors reconnect with bounded exponential backoff and write a reconnect counter to the manifest. A malformed message is retained in a quarantine/error record with source and receive time; it must not stop unrelated collectors. A source that is stale or disconnected is reported and excluded from tradable conclusions.

Downloaders use temporary files followed by an atomic rename, skip files whose checksum/size manifest already matches, and never overwrite existing validated raw data. Missing archive hours remain missing and are represented in the coverage report.

## Research output

The first analysis report contains:

- requested and available date ranges;
- per-source event counts and uptime;
- missing partitions and reconnects;
- event-time and receive-time sample counts;
- external shock definition and counts;
- Polymarket response at fixed horizons;
- confidence intervals using market-day blocks where sample size allows;
- an explicit `insufficient_data` or `exploratory` state when the data cannot support an economic conclusion.

A positive event-study response is not a trading recommendation. Fees, spread, depth, slippage, and latency are deferred to the execution-aware milestone.

## Testing and validation

Unit tests cover canonical serialization, timestamp ordering, partition naming, idempotent download decisions, timestamp-unit normalization, backward-only joins, and insufficient-data reporting. Tests use deterministic synthetic fixtures only to test logic; synthetic fixtures are never reported as market evidence.

Opt-in public smoke tests are separate from the default test suite because network availability and remote coverage are variable. Completion requires the default tests plus a local end-to-end fixture run that writes one partition, validates it, compacts it, and produces a report.

## Acceptance criteria

The milestone is accepted when:

- `Polymarket_5m_btc/` contains an installable project and no trading credentials or order code.
- A CLI invocation can validate a fixture and produce a Parquet partition plus a quality report.
- A collector can be started in public-data mode, reconnect without losing the process, and record local receive timestamps.
- Historical ingestion reports actual coverage before downloading and never fills unavailable periods.
- The lead-lag command refuses or labels data-insufficient inputs instead of reporting zero PnL as success.
- Tests and artifact validation pass with fresh command output.

## Follow-up boundary

After this milestone is verified, the next design/plan can add resolver-aware feature construction and calibrated q_external. Execution simulation, paper trading, and any live order path remain separate milestones.
