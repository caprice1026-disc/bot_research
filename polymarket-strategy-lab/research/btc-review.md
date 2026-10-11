# Independent BTC replay review — 2026-10-04

Scope: `btc_data.py`, the then-current `btc_backtest.py`, receive-time boundary,
visible depth, pending-leg accounting, fee units, and trailing-TWAP model math.

## Resolved implementation findings

- A first filled leg was discarded when the second leg expired or the market
  changed. The root implementation now records its directional payout/loss
  through `record_position` in timeout, unfilled-hedge, and end-of-data paths.
- The old end-of-data path stored infinity as settlement time, incompatible
  with `allow_nan=False`. Recorded positions now have finite settlement times.
- Sizing formerly estimated cost by multiplying rounded per-share fees.
  The implementation now reduces share quantity until the actual rounded
  aggregate fee respects available cash and the configured market budget.
- Reference schema now reads `coinbase_price`, the actual extracted midpoint,
  and both source age and receipt age are checked. This prevents newly received
  but stale source observations from being treated as current.
- The mean window now uses `(t−W,t]`, giving exactly 60 second samples for the
  60-second window. The known section of a partly completed final window uses
  the same open-left convention.
- Settlement tokens are compared with both recorded outcome-token identifiers;
  mismatches are rejected instead of silently becoming losing trades.

The extraction's five regression checks passed; the root replay's seven checks
also passed when run independently. Direct extracted-data checks confirmed
strictly prior receive times, one active market per second, uncrossed valid
quotes and positive executable top-level quantities. After the endpoint and
settlement-identifier fixes, all 15 BTC checks passed again using
`python -m unittest discover -s tests -p 'test_btc*.py' -v`.

## Fee-asset concern ruled out for V2

The current [official fee documentation](https://docs.polymarket.com/trading/fees)
defines fee = shares × rate × p × (1−p), crypto rate 0.07, and five-decimal
rounding. An [official Help article](https://help.polymarket.com/en/articles/13364471-maker-rebates-program)
still describes buy-side collection in shares, raising an apparent conflict.

The stronger implementation evidence is official
[`ctf-exchange-v2/Trading.sol`](https://github.com/Polymarket/ctf-exchange-v2/blob/main/src/exchange/mixins/Trading.sol):
`_settleTakerOrder` charges a buy fee from the buyer's collateral and deducts
sell fees from collateral proceeds. Its buy transfer includes both fill and
fee amounts. Official
[`ctf-exchange-v2/Fees.sol`](https://github.com/Polymarket/ctf-exchange-v2/blob/main/src/exchange/mixins/Fees.sol)
defines fees in collateral units. Thus additive collateral cost and full token
shares in this V2 research model are supported; changing this model to old
share deductions would be unwarranted. This is source-code evidence, not a
claim that any live order or contract probe was executed.

## TWAP model and non-vacuous checks

For a driftless arithmetic diffusion locally approximated from log returns,
the variance of a future trailing W-second average is proportional to
`T − 2W/3` when T≥W, and `T³/(3W²)` when only the final T<W seconds remain
unknown. The implementation uses these expressions. Known past prices and a
currently available oracle/exchange basis determine the conditional mean.
This is a model hypothesis, not empirical proof of calibration.

Independent `tests/test_btc_review.py` adds three passing checks:

1. Analytic probabilities before and within the final TWAP window, with fixed
   volatility and a nonzero strike difference.
2. A known actual UP signal must exist before appending a large future exchange
   and oracle spike; neither probability-driven side nor edge may change.
3. An old oracle timestamp or a missing second inside the required contiguous
   reference window must reject the calculation.

The original future-spike test was also checked for vacuity: its baseline was
an actual UP signal, not `None`. The independent test makes that precondition
explicit and still passes after the 60-sample endpoint correction.

## Remaining interpretation limits

- The opening oracle available strictly before the start may differ from the
  exact settlement strike. Reference/oracle staleness gates reduce but do not
  remove this basis and boundary-time uncertainty.
- The oracle/exchange basis is assumed stable over the remaining window.
  Probability calibration and robustness must be assessed on more sessions.
- Delayed displayed depth is not proof of a real fill; private queue state and
  market impact remain unobserved. Complete-set legs are deliberately sequential.
- Archive data with source timestamps only must retain a distinct event-time
  proxy label. It must not be represented as actual local historical receipt.

No live trading, wallet operation, or credential access was performed.
