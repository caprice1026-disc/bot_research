# Local data audit — 2026-10-04

Read-only inspection of existing datasets; no original data or runners were changed.

## BTC event archive

`Polymarket_5m_btc/data/runs/sqlite-2h-20260907T165500Z/events.sqlite3`
is 12,114,399,232 bytes and has 5,251,675 rows. SQLite source counts were queried
directly and agree with the completed collection manifest:

| Source | Rows |
|---|---:|
| Polymarket | 4,622,128 |
| Binance | 507,656 |
| Coinbase | 58,160 |
| Hyperliquid | 53,495 |
| Chainlink | 10,236 |

The receive-time index bounds are 2026-09-07 16:55:13.992505 through
18:55:12.451154 UTC. The final connection record is a cancelled disconnect at
18:55:18.459163 UTC, and the collector's completion manifest exists. This is a
completed two-hour acquisition, with `status=partial`, not an active collector.
Two recorded errors are market-discovery transport failures. All requested
sources are present; the stale channel is `polymarket/tick_size_change`.

The master contains 27 exact `btc-updown-5m-<epoch>` markets, including windows
before/after the observed interval. Only approximately 24 windows can contribute
full market histories. The master has slug, condition ID, UP/DOWN token IDs and
window bounds. It does not have outcomes, fees, minimum order sizes or initial
tick size. Actual resolved Gamma outcomes must be acquired separately.

The manifest reports 75,886 full book snapshots, 96,436 BBO messages,
4,413,452 depth changes and 36,020 trades. Sample rows confirm full depth lives
in `raw_payload.payload.bids/asks`; the normalized singular token update is
`raw_payload.price_change` with price/side/size. `source_event_ts` is integer
microseconds; `local_receive_ts` is ISO UTC. Separate UP and DOWN token IDs must
never be merged into one price series. Chainlink contains 30/60-second TWAPs.
The 60-second channel matches the current published resolution family; the
archive still lacks complete exact-boundary observations and contains source gaps.

The gap log has 2,015 records: 674 Polymarket quote receive gaps, 192 quote
connection changes, 493 trade receive gaps, and collection-end/other-channel
records. These should not all be interpreted as full-market outages. The
recorded quote gaps and connection changes must nonetheless invalidate a
reconstructed book until a fresh snapshot; quote age must be checked at entry
and delayed fill time. A two-hour sample does not establish profitability.

The older `stage-a-2h-20260907T053816Z` archive is also `partial` with 29 errors.
Its existing selector excluded nine 15-minute markets, retained 26 five-minute
markets, and explicitly reports `insufficient_data` for the configured
continuous decision windows. Prefer the newer SQLite acquisition.

## Weather artifacts

Direct normalized-file counts are 425 rules, 966 GEFS forecast members across
46 target dates, 605 observations, and 249 historical token prices. The price
manifest covers only seven markets for 2026-01-06, over 2026-01-05 12:00 through
2026-01-07 05:00 UTC. Historical prices have no executable bid/ask or depth.

Observations are from August 2025 and do not overlap the forecast target dates
(October 2025 through August 2026). Existing Level 1/2 backtest reports correctly
have zero trades and `insufficient_data`. The initial dataset manifest's
`forecast_members=0` is stale relative to the later successful GEFS manifest
and actual 966-row file. Use artifact contents and the relevant later manifest.

The normalized forecast `received_time` equals its historical issue time;
these are archive reconstructions, not locally timestamped historical receipt
evidence. Apply a forecast-publication delay and report this assumption.
Weather Gamma raw metadata exists under `data/raw/polymarket-new-york-markets-*`.
The latest inspected raw file (`20260808T170544Z`) contains 432 markets,
including 388 closed markets with exact binary outcome prices and 46 Yes
winners. These can supply payout labels without pretending NCEI observations
are the official resolution source; current CLOB winner flags should verify
the labels. Some impossible bins closed before the target day finished, so
exclude entries after each individual market's closed time.

## Auxiliary spot series

Unrelated untracked `polymarket-bonereaper-research/data/spot.csv` was inspected
without modification. It has 178,560 rows: 89,280 each for BTC and ETH, spanning
2026-08-03 00:00 through 2026-10-03 23:59 UTC. Each asset has unique timestamps
and every adjacent difference is exactly 60 seconds. Its manifest says daily
Binance spot ZIP checksums were verified. These are spot bars, not Polymarket
prices or resolution labels. The close is only available after timestamp + 60.

## Reusable environment and acquisition paths

`Polymarket_5m_btc/.venv/Scripts/python.exe` has pandas, numpy, httpx and pyarrow.
It does not have scipy or duckdb. A weather-specific Python venv also exists.

Existing code supports Gamma `/markets` and `/public-search`, CLOB
`/batch-prices-history`, NCEI global-hourly date chunks and the NOAA GEFS S3
archive. CLOB token-price histories are proxy prices, not order books.
`Polymarket_5m_btc/src/btc5m/ingestion/pmxt.py` has a bounded PMXT coverage and
download utility targeting `https://r2v2.pmxt.dev/polymarket_orderbook_<UTC-hour>.parquet`.
These are code-discovered endpoints; current availability must be verified
before relying on them. No endpoint availability was assumed from old manifests.

## New read-only extraction

`btc_data.py` reconstructs full books and singular depth changes from the newer
database in receive-time order. UTC second T only includes events received
strictly before T. Invalid books expose null quotes/sizes, while stale valid
states retain explicit ages for the execution model to reject. Connection
changes, receive gaps over five seconds, empty/crossed books and mismatches
against reported BBOs require another complete snapshot. It also preserves
Binance/Coinbase midpoints and the two named Chainlink TWAP channels.

The completed extraction manifest records source SHA256, source bounds,
original errors, gap count, rejection counts and output SHA256. Five focused
regression checks pass. The actual CSV contains 7,198 second-level rows over 25
active slugs, with 5,392 valid paired books (all receive ages at most two
seconds). Direct output checks confirmed strictly prior receipt times,
single active markets per second, positive quantities and uncrossed books.
All 75,886 source snapshots and 4,413,452 source depth changes were processed.
20,422 BBO mismatches were conservatively invalidated until another snapshot.

Source SHA256:
`c5f0205649a8f9a4b254c794b61907d920b23b9b629a3b71d5bac4b9539f8c95`.
Output SHA256:
`5999d1d3b655ccda154ef19159129325eca0d80414154515c76cc0f0cbb4c3e1`.
Minimum valid simultaneous UP ask + DOWN ask is 1.01 before fees; the observed
sample therefore has no profitable immediate complete-set taker purchase.
Coinbase source-age maximum is 3.24 seconds; Chainlink TWAP source-age maximum
is 618 seconds. Downstream strategies must apply their reference freshness gates.
