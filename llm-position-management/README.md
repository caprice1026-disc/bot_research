# LLM position management

This package is an offline, stateful position-management experiment.  A five-minute slot is a decision opportunity, not an automatic close.  The v1 contract accepts only a discrete target position, validates it against fixed Decimal Risk limits, and sends only the delta between the current and frozen target quantities to the simulator.

It currently provides a deterministic fixture replay only.  It does not call Gemini, place a Hyperliquid order, or turn a failed response into a position change.

From the repository root, install the two local packages once into the existing environment:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pip install -e .\trading-core -e .\llm-position-management

Then run the checked-in lifecycle fixture:

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.cli replay --config .\llm-position-management\configs\fixture.json --output .\llm-position-management\data\fixture-run

The output directory is created atomically and contains a complete manifest, observations, decisions, every simulated account event (including an SL/TP-style protective fill between decision slots), final equity snapshot, and a Markdown report.  SQLite uses WAL mode and persists the latest account state, decisions, consecutive response failures, and model-cost reservations before a policy call; a resumed report therefore includes the entire run, not only decisions made after restart.  The CLI refuses to overwrite an existing run directory.

The fixture demonstrates open, hold, add, reduce, and close.  Any valid delayed response is applied only at the first recorded market tick at or after its receipt time; a position change, SL, funding, or hard Risk close while waiting invalidates that response.  New exposure is rejected if the market stream has a gap longer than `max_market_gap_ms` (60 seconds by default); a safety close waits for the earliest later market tick instead of using a pre-response price.  It is a behavior test, not a profitability result and not a Testnet run.
