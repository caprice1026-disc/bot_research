# LLM position management

This package is an offline, stateful position-management experiment.  A five-minute slot is a decision opportunity, not an automatic close.  The v1 contract accepts only a discrete target position, validates it against fixed Decimal Risk limits, and sends only the delta between the current and frozen target quantities to the simulator.

It currently provides a deterministic fixture replay only.  It does not call Gemini, place a Hyperliquid order, or turn a failed response into a position change.

From the repository root, install the two local packages once into the existing environment:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pip install -e .\trading-core -e .\llm-position-management

Then run the checked-in lifecycle fixture:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.cli replay --config .\llm-position-management\configs\fixture.json --output .\llm-position-management\data\fixture-run

The output directory is created atomically and contains a complete manifest, observations, decisions, every simulated account event (including an SL/TP-style protective fill between decision slots), final equity snapshot, and a Markdown report.  SQLite uses WAL mode and persists the latest account state, decisions, consecutive response failures, pending safety closes, market-data completeness, and model-cost reservations before a policy call; a resumed report therefore includes the entire run, not only decisions made after restart.  The CLI refuses to overwrite an existing run directory.

The fixture demonstrates open, hold, add, reduce, and close.  Any valid delayed response is applied only at the first recorded market tick at or after its receipt time; a position change, SL, funding, or hard Risk close while waiting invalidates that response.  A missing response that reaches the safety-close threshold saves a pending close and executes it at the next recorded tick, including after a restart.  If an SL has already closed that position at the next tick, the runner records `safe_close_already_flat` and still consumes that slot, so a duplicate tick cannot reopen exposure.  New exposure is rejected if the market stream has a gap longer than `max_market_gap_ms` (60 seconds by default).  Every tick interval, including a restart boundary, is checked; a gap changes the report status to `partial` because an intervening stop or fill cannot be reconstructed.  A pending safety close also makes the report `partial`.  The checked-in five-minute fixture explicitly sets `max_market_gap_ms` to 300 seconds; real one-minute data should retain the 60-second limit.  It is a behavior test, not a profitability result and not a Testnet run.

When resuming an older DB, three or more consecutive failures with an open position imply a pending safety close even if the stored flag is false or was absent before migration. Partial reductions validate the remaining position's stop against the execution-time market price; full closes remain available without a stop.

## Real-data functional acceptance

The shared `trading_core.market_data.replay_inputs` reader checks complete, ordered BTCUSDT Binance one-minute candles and eight-hour Funding. It rejects gaps, duplicates, wrong markets, and unavailable candle timestamps. Existing downloaded inputs can be reused without network calls:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.scripted_replay --candles hyperliquid-ai-trader/data/research/binance-BTCUSDT-1m.jsonl --funding binance-btcusdt-futures-research/data/research/BTCUSDT-funding-2025-09-01_2026-08-31.csv --start 1756684800000 --end 1756771200000 --output llm-position-management/data/real-replay-20250901

After the day passes, use `--end 1759276800000` and a new output directory for September 2025. Bounds are UTC candle-open times, with the end exclusive. Inputs and settings are hashed; existing outputs cannot be overwritten. Each run compares all decisions, events, costs, and final account state with a SQLite restart halfway through, including an overlapping tick.

If the process stops, use the same arguments plus `--resume-incomplete <the .output-name.tmp-... directory>`. The runner verifies the saved configuration fingerprint, skips completed history, and resumes at the last persisted tick. It publishes the final directory only after the two results agree.

The fixed six-hour script alternates LONG/SHORT and exercises open/hold/add/reduce/close with a 2% initial stop, a 250 USD anchor, 0.045% one-sided fee, 2 bps spread, and 1 bp slippage. It is a functional test, not an optimized strategy. Funding uses actual historical rates with minute-open prices as proxies; sub-minute settlement timestamps are rounded forward to the next completed minute. Minute OHLC cannot resolve exact settlement/stop ordering or exchange mark prices. These approximations and the Binance/Hyperliquid venue difference preclude treating this as execution-accurate profitability evidence.
