# Independent weather review — 2026-10-04

Reviewed the stable `weather_data.py` and `weather_backtest.py`, followed by
their completed source manifest and generated trade-tape results.

## Availability and information boundaries

The forecast source is a named historical Single Runs model initialization,
not a current forecast. Model initialization plus six hours is an explicit
availability assumption. The 10:00 Chicago decision follows that assumption;
the actual archive does not prove the exact historical distribution timestamp.
Only calibration days ending 2026-08-30 fit the model weights, bias and sigma.
Out-of-sample labels do not change the fit in an independent perturbation test.

IEM observations use KORD routine reports. A temperature contributes to the
dead-bucket signal only after the configured 30-minute availability lag.
The archive can contain future observations, but they do not enter an earlier
decision. The independent test starts from a real baseline fill, then moves
the decisive observation behind the release boundary and confirms no signal.

Historical Gamma titles/rules determine contract identity. Resolved prices
and chain resolution data supply payouts; their current retrieval does not
make them decision-time forecasting features. The August source transition
from Wunderground to NOAA is preserved. All OOS markets use the NOAA family.
The report records three daily IEM/settlement-bin mismatches rather than
silently replacing the actual market payout labels.

## Findings fixed during review

- Trading stop was previously checked only against final resolution time.
  Both decisions and delayed fills now use the earlier of Gamma `closedTime`
  and chain resolution time. Gamma `endDate` at noon UTC is not confused with
  the local temperature-day trading cutoff.
- Fees now use five decimal places with Decimal half-up rounding.
- The capital requirement conservatively covers every possible order without
  depending on interim settlements. An independent low-capital test confirms
  rejection before trading.
- A cash ledger now reserves actual entry costs and releases actual payouts
  at resolution timestamps. Settlement-order drawdown follows those timestamps,
  while the bootstrap deliberately groups PnL by weather target date.

Eight independent tests in `tests/test_weather_review.py` pass: fee precision,
first eligible BUY print without a favorable subsequent-print search, strict
price-before-decision, future observation rejection, OOS calibration separation,
early close at decision and execution, insufficient capital, and overlapping
position cash/reserve reconciliation.

## Actual artifact verification

The completed source manifest has 60 requested and observed dates, 660 markets,
93,852 raw trade rows, and no collection errors. All 60 observation days cover
24 local hours. The frozen config hash agrees with the collected manifest.

Direct result checks found 33 unique strategy/day attempts and one base-case
proxy fill. Each signal precedes its decision. The fill satisfies configured
latency, expiry, trade-size participation, and both closing cutoffs. The source
transaction hash, chosen outcome token, BUY side, price, quantity and timestamp
match the actual cached trade record. Cost and payout arithmetic reconcile.

Under the explicitly assumed trade-tape model, the OOS forecast strategy has
one fill and a loss of 0.2096 collateral units; the dead-bucket strategy has
zero attempts. Both are correctly `insufficient_out_of_sample_fills`. Neither
zero trades nor the one fill validates profitability or proves live execution.

## Supplemental order-book work

`weather_book_check.py` separately requests actual public archive L2 data for
the existing 20 OOS forecast attempts. This conditions on the trade-tape signal
selection and therefore cannot represent the full L2 strategy by itself.

The initial four-minute request often lacked a starting snapshot. Acquisition
was expanded to the entire preceding hour and two following minutes, including
full books, price changes, and BBO messages. Quiet weather books are not treated
as proven feed disconnections. Reconstruction can reconcile a transient BBO
mismatch after the corresponding actual depth delta arrives; a delta without
any starting snapshot still cannot create depth. Three focused tests verify
these rules and strict decision-boundary exclusion.

The audit reports 5/60/300-second age sensitivities, preserving actual quote
and snapshot ages. Archive continuity and hypothetical bot latency remain
assumptions; private queue priority and real fills are unobserved. Consult
`research/weather_book_check/report.json` for completed audit counts.
