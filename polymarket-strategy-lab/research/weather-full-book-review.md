# Whole-cohort weather L2 acquisition review

The complete follow-up cohort is August 31 through September 29, 2026: 30 days,
330 Chicago temperature conditions, and 660 distinct Yes/No outcome snapshots.
This period was already examined in the trade-print study and supplemental
20-attempt audit. It is exploratory evidence, not an untouched holdout.

## Frozen scope and sources

`weather_full_book_data.py` selects all 11 conditions per day without consulting
forecast signals or winners. The normal archive request is 14:00–15:02 UTC.
Actual full snapshots, BBO, price changes, and tick changes are retained. Only
August 31 required one additional preceding hour to obtain all starting books;
the observed maximum lookback was two hours. A missing seed is never interpreted
as no liquidity. Historically closed conditions are excluded from optional
seed backfill, although none were closed before these 15:00 decisions.

Source partitions are public Pendulum Flow v3 hourly Parquet files, for example
[August 31 at 14 UTC](https://dl.pendulumflow.com/v3/2026-08-31/14/2026-08-31T14.parquet),
with the [published manifest](https://archive.pendulumflow.com/v3/2026-08-31/14/manifest.json).
The 61 selected local partitions contain 874,270 rows. Each has an independently
verified local SHA-256, source URL, published full-file hash, and source manifest
hash. The remote full hourly file is not independently downloaded or hashed.

`research/weather_full_book_acquisition_config_v2.json` freezes 60 seconds as
the primary maximum receive/source quote age, with 5 and 300 seconds secondary.
It was saved before full-cohort PnL evaluation. Version 1 is preserved along
with preliminary v1 snapshots and their manifest. Version 2 documents the
reconstruction fixes below; no threshold was selected by profitability.

## Causality and repaired reconstruction

The 15:00 signal sees only events with archive receive time strictly before
15:00. The 15:01 execution snapshot similarly excludes events received exactly
at or after 15:01. Depth changes follow actual receive order. A future snapshot
cannot initialize an earlier signal; the missing historical seed must be fetched.

The shared BTC book initially rejected any one-sided state and stopped applying
depth changes after a temporary empty/crossed state. This was inappropriate for
weather contracts and could retain a false invalid state. The repair is opt-in
through `allow_one_sided=True` in the full weather extractor. BTC defaults and
the original 20-attempt audit remain unchanged. Actual deltas continue to apply,
and reconstructed top prices must agree with the reported BBO. Sentinel bid 0
or ask 1 represents an absent side, never an executable order. A BUY still needs
a real ask and positive observed size.

Two regression tests reproduce one-sided depth and temporary crossed-state
recovery. Additional acquisition tests require all 660 unique token mappings,
reject duplicate tokens, fetch an earlier seed despite a post-decision snapshot,
and skip pointless seed backfill for a historically closed market. The final
complete lab suite passes 47 tests, including 16 BTC tests that retain the
original default behavior and all acquisition, weather, and expanded-data tests.

## Verified acquisition result

The final manifest is `research/weather_full_book_data/manifest.json` and the
dataset is `data/weather_full_books/snapshots.jsonl`. All 660 outcomes have an
initial snapshot; all recorded latest receive times precede their sample times.
All 61 subset hashes, scope hash, and extractor hashes were checked against the
actual saved files. There are no acquisition errors.

| Maximum quote age | Signal asks available | Delayed asks available |
| --- | ---: | ---: |
| 5 seconds | 127 | 200 |
| 60 seconds, primary | 482 | 492 |
| 300 seconds | 515 | 515 |

These counts require a valid observed ask and source/receive freshness but do
not impose the strategy's five-share depth, probability-edge, or price-limit
checks. At the signal time, 145 outcomes have no actual ask. Their missing ask
is retained as null, rather than substituted with a fabricated price.

- Final snapshot SHA-256: `d0ef6cd5333d8520da2762ff00c019ba213635fa99343e314d5b55cd6ee2262e`.
- V2 scope SHA-256: `c47a1f43c4d12e71a2f9f66363a6e4f170dcd02d50afb52db1ad797918e242f4`.

## Remaining interpretation limits

Archive receive time is the collector's, not a hypothetical trader's. Delivery
continuity between events is assumed, and quiet periods do not prove a feed
disconnect. Reconstructed displayed depth does not prove actual live execution.
Historical initial tick and minimum-order metadata may be unavailable; current
metadata fields are explicitly reference-only. Five-share conditional fills,
current fees, and adverse cost buffers remain model assumptions. Forecast
publication lag, station/model mismatch, dependent days, and the reused evaluation
period remain limitations of any subsequent strategy result.

The independent code review of `weather_full_book_backtest.py` found signal-time
ranking separated from delayed execution, distinct actual Yes/No asks, one attempt
per day, source and receive freshness gates, settlement/close cutoffs, and a cash
ledger that keeps cost reserved until actual resolution. The result writer checks
cash against the independent summary ledger. Evaluation results belong in
`results/weather_full_books`; this acquisition review itself makes no PnL claim.
