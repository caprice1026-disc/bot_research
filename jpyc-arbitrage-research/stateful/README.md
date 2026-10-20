# Latest addition: one-yen redemption and capital recycling

See `REDEMPTION_RESULTS_JA.md` for the user-requested one-yen redemption comparison, 80 scenarios, and cash-queue accounting. Run `python stateful/run_redemption.py`; test with `cd stateful && python -m unittest test_redemption -v`. The earlier no-recycling / DEX-exit experiment below remains as a separate comparison, not the only strategy.

# JPYC stateful Paper replay

This is an offline, bounded counterfactual simulation, not a live trading bot. It sends no transactions, holds no credentials and performs no redemption. The independent historical quote scan has now been extended to a stateful replay that retains the effect of hypothetical purchases.

## Run

Keep this directory beside `replay_data`, `public_rpc/grid`, and `reference`:

```sh
python stateful/run_stateful.py
cd stateful && python -m unittest test_stateful -v
```

Python standard library only. `results/summary.csv` and `summaries.json` are the scenario outputs. Each scenario has transaction and observation/decision CSVs (UTF-8 BOM). `results/quoter_validation.csv` audits all 1,140 historical quantity quotes. `RESULTS_JA.md` explains the findings.

## Verified data and mathematical model

- Polygon chain 137; current redeemable JPYC, not the old prepaid token. USDC has 6 decimals, JPYC 18.
- Uniswap v4 USDC/JPYC pool ID `0x4643e3152a6ea3957e087cefbba74a1c628aa643e6b8a0edfae23a5796dde012`.
- No hook, tick spacing 10, LP fee 500 pips plus protocol 125 pips. Integer-combined swap fee 625 pips (0.0625%). Fee and own price impact are included in the integer swap calculation, not subtracted again.
- Initial state is **after block 95802902**, 2026-10-19 11:35:00 UTC. Replay starts at 95802903 and ends at 95859621, 2026-10-20 11:13:20 UTC.
- 149 logs: 120 Swap, 29 ModifyLiquidity. Twelve contiguous, non-overlapping RPC ranges; no duplicates or unknown event types. Event block hashes checked. No Donate or protocol-fee change events occurred in the collected pool logs.
- Initial bitmap words 126–128 contain 108 initialized ticks. These are a bounded known range (ticks 322560–330230), not the entire pool. Any traversal beyond this range raises an error and stops the scenario. Liquidity changes at known ticks are applied; active liquidity changes according to the **counterfactual** tick.
- Baseline with no hypothetical trades retains its own computed state throughout. All observed swap tick and liquidity values match. Maximum sqrt-derived price discrepancy is 8.84e-11 bps, attributable to replaying gross observed inputs without original exact-output/price-limit instructions.
- At each of 285 observed snapshots, using the observed sqrt/tick and reconstructed liquidity map, all four historical Quoter outputs match exactly in raw integer units (1,140/1,140). This observed-state reset is used **only in the validation check**, never in the simulated strategy.
- Mathematical formulas follow the MIT Uniswap v4 `TickMath`, `SqrtPriceMath`, `SwapMath` libraries and pool traversal semantics. Local integer arithmetic reproduces uint rounding; no floating-point price stepping.

## Explicit counterfactual assumptions

1. All subsequent external swaps use the observed gross input quantity and direction. The actual transaction might have been exact-output or have a price limit; this information is not recoverable from the Swap event alone. We do not claim to reproduce those instructions. External traders, arbitrageurs and LPs do not react to our hypothetical transactions; this is a fixed-order replay, not an equilibrium simulation.
2. LP changes occur at the observed block/order, fixed tick ranges and liquidity quantities. We do not replay LP fee accounting because it does not affect swap price/liquidity under these fixed-position assumptions.
3. At each of the 285 sampled times, all real events through the corresponding historical block are applied first. The bot then hypothetically executes immediately on that state. Inclusion delay, competition, MEV, reverts, approve, and price movement between decision and inclusion are omitted.
4. Each policy has finite capital of 100 or 1,000 USDC. Gas POL is separate. Cash is deducted on every buy, and acquired JPYC remains inventory. There is **no redemption, capital replenishment, borrowing, instant recycling or invented infinite capital**.
5. Fixed-size policies test 1, 10 and 100 USDC per observation, buying only if the estimated one-yen reference surplus after gas is strictly positive and cash is sufficient. The `greedy_grid` policy chooses the largest positive absolute surplus among affordable 1/10/100/1000 USDC candidates. This is optimal only within that preset grid, not across all continuous amounts. Fixed-size policies are comparisons, not post-hoc changes to one strategy.
6. Funding and transfer costs are zero as requested. Swap fees and impact remain included. Gas is a scenario, not an actual router simulation or guaranteed upper bound. Base: max(historical Quoter gas + 150k + 10k × extra steps, 250k), with historical baseFee + 30 gwei. High: corresponding overhead 300k/floor 500k/tip100gwei. Historical POL/JPY converts gas to yen.
7. Conversion references are CoinGecko USDC/JPY and POL/JPY samples with source timestamps no later than the decision. Publication latency, revisions and executable FX spread are unverified. These are not guaranteed executable yen prices.
8. JPYC=1 yen is a reference redemption valuation, not an executed redemption. Eligibility, minimums, processing time, issuer risk and practical settlement are not simulated. A separate hypothetical end-of-window sale uses the same **counterfactual** pool, with fee, price impact and an extra gas scenario; it is not an actual sale.

## Read the profit columns correctly

- `acquisition_reference_surplus_jpy` = sum(JPYC received − USDC spent × **that purchase's** USDC/JPY − purchase gas). This describes reference-value advantages at acquisition, not one common-date portfolio profit.
- `ending_one_yen_valuation_vs_hold_usdc_jpy` = JPYC inventory at 1 yen + remaining USDC × **ending** FX − initial USDC capital × ending FX − purchase gas.
- `hypothetical_dex_exit_vs_hold_usdc_jpy` = (remaining USDC + hypothetical final sale USDC − initial USDC capital) × **ending** FX − purchase gas − final sale gas.
- The last two columns use the same end-of-window hold-USDC benchmark and can be compared directly. They differ by the JPYC valuation/exit assumption, not by their FX timestamp.
- `ending_one_yen_valuation_vs_initial_jpy` instead compares with starting yen valuation, so includes the change in USDC/JPY; do not mix it with the hold-USDC benchmark.
- `realized_profit` is null: no actual trade/redemption occurred.

## Sources

- https://docs.polygon.technology/pos/reference/rpc-endpoints
- https://developers.uniswap.org/docs/protocols/v4/deployments
- https://raw.githubusercontent.com/Uniswap/v4-core/main/src/libraries/TickMath.sol
- https://raw.githubusercontent.com/Uniswap/v4-core/main/src/libraries/SqrtPriceMath.sol
- https://raw.githubusercontent.com/Uniswap/v4-core/main/src/libraries/SwapMath.sol
- https://raw.githubusercontent.com/Uniswap/v4-core/main/src/libraries/Pool.sol
- https://raw.githubusercontent.com/Uniswap/v4-core/main/src/interfaces/IPoolManager.sol

Historical archive reads were obtained through the public Tenderly Polygon RPC listed in the official Polygon documentation. No API key or wallet was used.
