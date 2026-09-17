# Trading core

`trading_core` holds the venue-neutral, Decimal-safe pieces used by local trading experiments: target-position delta planning, execution-cost accounting, and an offline single-position simulator.  It has no network dependency and does not import a particular experiment package.

The initial public behavior is exercised through `trading-core/tests` and the fixture runner in `llm-position-management`.  Existing Hyperliquid research modules remain unchanged while their compatibility baseline is preserved.
