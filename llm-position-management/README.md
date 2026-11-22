# LLM position management

This package is a stateful position-management experiment. A five-minute slot is a decision opportunity, not an automatic close. The v1 contract accepts only a discrete target position, validates it against fixed Decimal Risk limits, and sends only the delta between the current and frozen target quantities to the simulator.

The fixture CLI is offline. The explicit `comparison` CLI can call Gemini under a shared hard budget; neither CLI places exchange orders. Failed responses never become ordinary target-position orders.

From the repository root, install the two local packages once into the existing environment:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pip install -e .\trading-core -e .\llm-position-management

Then run the checked-in lifecycle fixture:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.cli replay --config .\llm-position-management\configs\fixture.json --output .\llm-position-management\data\fixture-run

The output directory is created atomically and contains a complete manifest, observations, decisions, every simulated account event (including an SL/TP-style protective fill between decision slots), final equity snapshot, and a Markdown report.  SQLite uses WAL mode and persists the latest account state, decisions, consecutive response failures, pending safety closes, market-data completeness, and model-cost reservations before a policy call; a resumed report therefore includes the entire run, not only decisions made after restart.  The CLI refuses to overwrite an existing run directory.

The fixture demonstrates open, hold, add, reduce, and close.  Any valid delayed response is applied only at the first recorded market tick at or after its receipt time; a position change, SL, or hard Risk close while waiting invalidates that response. Funding updates cash before the execution-time Risk check.  A missing response that reaches the safety-close threshold saves a pending close and executes it at the next recorded tick, including after a restart.  If an SL has already closed that position at the next tick, the runner records `safe_close_already_flat` and still consumes that slot, so a duplicate tick cannot reopen exposure.  New exposure is rejected if the market stream has a gap longer than `max_market_gap_ms` (60 seconds by default).  Every tick interval, including a restart boundary, is checked; a gap changes the report status to `partial` because an intervening stop or fill cannot be reconstructed.  A pending safety close also makes the report `partial`.  The checked-in five-minute fixture explicitly sets `max_market_gap_ms` to 300 seconds; real one-minute data should retain the 60-second limit.  It is a behavior test, not a profitability result and not a Testnet run.

When resuming an older DB, three or more consecutive failures with an open position imply a pending safety close even if the stored flag is false or was absent before migration. Partial reductions validate the remaining position's stop against the execution-time market price; full closes remain available without a stop.

[Gemini 3.6 Flash移行・料金・旧実験の扱い・検証範囲](../docs/gemini-36-migration-20261011.md)

## Real-data functional acceptance

The shared `trading_core.market_data.replay_inputs` reader checks complete, ordered BTCUSDT Binance one-minute candles and eight-hour Funding. It rejects gaps, duplicates, wrong markets, and unavailable candle timestamps. Existing downloaded inputs can be reused without network calls:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.scripted_replay --candles hyperliquid-ai-trader/data/research/binance-BTCUSDT-1m.jsonl --funding binance-btcusdt-futures-research/data/research/BTCUSDT-funding-2025-09-01_2026-08-31.csv --start 1756684800000 --end 1756771200000 --output llm-position-management/data/real-replay-20250901

After the day passes, use `--end 1759276800000` and a new output directory for September 2025. Bounds are UTC candle-open times, with the end exclusive. Inputs and settings are hashed; existing outputs cannot be overwritten. Each run compares all decisions, events, costs, and final account state with a SQLite restart halfway through, including an overlapping tick.

If the process stops, use the same arguments plus `--resume-incomplete <the .output-name.tmp-... directory>`. The runner verifies the saved configuration fingerprint, skips completed history, and resumes at the last persisted tick. It publishes the final directory only after the two results agree.

The fixed six-hour script alternates LONG/SHORT and exercises open/hold/add/reduce/close with a 2% initial stop, a 250 USD anchor, 0.045% one-sided fee, 2 bps spread, and 1 bp slippage. It is a functional test, not an optimized strategy. Funding uses actual historical rates with minute-open prices as proxies; sub-minute settlement timestamps are rounded forward to the next completed minute. Minute OHLC cannot resolve exact settlement/stop ordering or exchange mark prices. These approximations and the Binance/Hyperliquid venue difference preclude treating this as execution-accurate profitability evidence.

## Precise Funding and frozen comparison

`trading_core.market_data.binance_replay` captures public Binance candles and Funding history, including actual `fundingTime` and settlement `markPrice`. Unlike the older CSV replay above, the new route retains exact Funding time/price. Completed-bar stops precede Funding at the bar-end boundary; an intrabar Funding/stop collision is explicitly flagged and evaluated with adverse cashflow ordering. It cannot establish the actual intraminute stop execution time without trade-level data.

The experiment compares no-trade, fixed script, Gemini without resizing (open/hold/close only), and Gemini with resizing, in separate accounts. All market orders wait for the first completed minute at or after receipt. Terminal closure is fixed in advance. All metrics include trading costs; net-after-API and DD-after-API are reported separately. Holding time uses flat-to-flat lifecycles, and turnover is the sum of absolute executed notionals (not twice that value).

Run from the repository root with the existing environment and `GEMINI_API_KEY` in the environment or root `.env`. Optional SDK dependencies are declared under `[gemini]`. No key is copied to artifacts.

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m trading_core.market_data.binance_replay --start 1788220800000 --end 1788480000000 --output trading-core/data/btcusdt-20260901-03.json
    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.comparison --data trading-core/data/btcusdt-20260901-03.json --output llm-position-management/data/comparison-20260903-v3 --api-ledger llm-position-management/data/comparison-20260902-v2/api --arm canary --budget 10 --max-slots 0

After the canary passes, change `--arm` to `no_trade`, `fixed_rule`, `llm_no_resize`, or `llm_resize`. The final comparison period is September 3, 2026 UTC. The September 2 cohort was stopped after discovering a hold/null schema defect and remains partial. The explicit `--api-ledger` keeps both cohorts and canaries under one budget. The LLM arms require the passed canary report. Use `--max-slots 72` for bounded chunks and repeat the same command until `period_complete=true`; saved slots are never submitted again. Existing completed outputs are regenerated from persisted decisions without paid calls. `freeze.json` fixes input hashes, prompt, all response-schema variants, settings, model, and period before the first paid request. The shared SQLite budget reserves before sending, counts unknown outcomes conservatively, and atomically enforces the 10 USD cap across accounts. SDK retries are disabled. A crash with an unresolved runner request freezes ordinary execution and safely closes surviving exposure rather than silently resending.

This comparison is a one-day historical experiment, not evidence of future profitability. The unused period is unused by the prior repository studies, not guaranteed absent from model training. Historical model knowledge and minute execution approximations remain limitations. Live shadow and Testnet are not part of this command.
