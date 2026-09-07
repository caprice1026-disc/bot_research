# Polymarket BTC 5m research

This project captures public market data and studies short-horizon lead-lag between external BTC venues and Polymarket BTC Up/Down 5m markets. It does not place orders, read wallet credentials, or claim that a fixture result is a trading edge.

## Setup in PowerShell

From this directory:

    py -3.13 -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    .venv\Scripts\python.exe -m pip install -e .

The project intentionally uses `pip` and requirements files; no `uv.lock` is required.

Every live row retains `sequence_id` as the source-side duplicate candidate and
`receive_id` as a local ingestion identifier. They are intentionally different:
a replay should have a new `receive_id` while keeping the same source sequence.
Polymarket rows also retain `market_id` and token `symbol`, so UP and DOWN
observations are never treated as one price series. `connection_id` identifies
the websocket/SDK connection that delivered the row and is not a duplicate key.

## Checks

    .venv\Scripts\python.exe -m pytest -q
    .venv\Scripts\ruff.exe check src tests
    .venv\Scripts\pyright.exe
    .venv\Scripts\python.exe -m btc5m --help

## Offline fixture

The fixture checks staging JSONL, ZSTD Parquet compaction, quality validation, and receive-time lead-lag report generation. Its report state is `exploratory`; it is not market evidence.

    .venv\Scripts\python.exe -m btc5m fixture --output-root .\data\fixture-run

## Public collection

Collectors are bounded by duration and use public feeds only. `chainlink` is collected through the Polymarket public SDK subscription; `polymarket` collects the discovered BTC 5m token books.

    .venv\Scripts\python.exe -m btc5m collect --sources all --duration-seconds 60 --output-root .\data\raw_staging

The Polymarket/Chainlink collector refreshes market discovery periodically and
re-subscribes with current and newly discovered five-minute token IDs. Its
`collection_manifest.json` is scoped to the current run: `ok` requires events
from every requested source and no recorded errors; `partial`, `no_events`, or
`error` must not be used as research success. The `collect` command also exits
with code 1 for those non-`ok` states. Market subscription and Chainlink
subscriptions are separate: market refresh does not restart Chainlink. The
run also writes `logs/connections.jsonl` (UTC time, source, connection ID,
state, reason), `logs/gaps.jsonl` (channel receive gaps), and records SDK
subscription `dropped` counters when the installed SDK exposes them. Use
`--gap-threshold-seconds` to control the interval threshold independently of
the final stale-source threshold.

If only external venues are needed:

    .venv\Scripts\python.exe -m btc5m collect --sources binance,coinbase,hyperliquid --duration-seconds 60 --output-root .\data\raw_staging

## Exact 5-minute market selection

The raw run is never overwritten. The selector accepts only the exact
`btc-updown-5m-<epoch>` slug, so `15m` markets are excluded even if a catalog
question contains the text `5m`. It streams event JSONL instead of loading a
multi-gigabyte run into memory, writes a separate selected raw tree, and marks
each market eligible only when the lookback/history window through the largest
evaluation horizon is continuous. Chainlink 60-second features require the
configured history window as well.

    .venv\Scripts\python.exe -m btc5m select-5m --input-root .\data\runs\stage-a-2h-20260907T053816Z --output-root .\data\selected\stage-a-2h-20260907T053816Z --copy-events

`selection_manifest.json` contains selected/excluded market IDs, per-channel
first/last receive time, gap intervals, and `insufficient_data` when no market
passes continuity. Without `--copy-events`, the same manifest is produced as
an index while leaving the raw input in place; `--copy-events` creates the
explicit filtered copy under `output-root/raw`.

## Historical coverage

The default PMXT command only inspects coverage. `--download` is explicit and should be used with a bounded interval after checking the manifest.

    .venv\Scripts\python.exe -m btc5m ingest-pmxt --start 2026-08-10T00:00:00Z --end 2026-08-10T03:00:00Z --output-root .\data\pmxt
    .venv\Scripts\python.exe -m btc5m ingest-pmxt --start 2026-08-10T00:00:00Z --end 2026-08-10T01:00:00Z --download --output-root .\data\pmxt

Historical archive rows have source timestamps but no local receive timestamp. They are suitable for event-time or proxy analysis only; receive-time conclusions remain `insufficient_data` until the prospective collector has recorded the feeds.

## Research commands

    .venv\Scripts\python.exe -m btc5m compact --input .\data\raw_staging\binance\date=YYYY-MM-DD\hour=HH\events.jsonl --output .\data\normalized\binance.parquet
    .venv\Scripts\python.exe -m btc5m validate --input .\data\normalized\binance.parquet --output .\reports\quality.json
    .venv\Scripts\python.exe -m btc5m lead-lag --external .\data\normalized\binance.parquet --polymarket .\data\normalized\polymarket.parquet --output .\reports\lead_lag.json

The lead-lag analysis uses backward/as-of receive-time joins and reports `insufficient_data` when local receive timestamps or overlapping responses are unavailable. It does not include fees, slippage, execution, or live trading.

The full Japanese research scope is in [RESEARCH_PLAN.md](RESEARCH_PLAN.md). The implementation plan is in the repository root `.agent/` directory.
