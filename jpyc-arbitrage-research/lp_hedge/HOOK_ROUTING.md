# JPYC v4 hook pool: routing conditions

Verified on 2026-10-20 (UTC). Read-only documentation and source review; no deployment, swap, fork test, or revenue measurement was performed.

## When external sell orders can reach a new pool

Uniswap routing can split a swap across multiple routes. A new pool is not entitled to a fixed share: it needs to be an eligible, discovered candidate and improve the gas-adjusted quote for some order size. Active liquidity around the execution price, fees, price impact, hook execution cost, quote compatibility, and the caller's protocol/router settings matter. Liquidity outside the relevant range does not provide the same execution depth. Eligibility is not a guarantee of indexing latency, route selection, or volume.

The current official Labs guidance says most hooks are automatically considered. Manual review and allowlisting are required if **either**:

- The hook's address starts with `0x91…`.
- It uses `beforeSwapReturnsDelta`, `afterSwapReturnsDelta`, or `dynamicFees`.

Ordinary `afterSwap` is distinct from `afterSwapReturnsDelta`: using the former alone does not trigger those stated exceptions. Manual-review criteria include supported-mainnet deployment, verified source, no proxy upgrades, no bypass/modification of protocol fees, no malicious/extractive behavior, and no custom calldata requiring router modification. Allowlisting is discretionary and not a security endorsement. Hooklist metadata is useful for presentation, but is not itself a routing guarantee. Other aggregators have their own integration policies.

Sources:
- https://support.uniswap.org/hc/en-us/articles/48291859140621-Routing-for-hooked-pools
- https://developers.uniswap.org/docs/ecosystem/builder-support/build-with-us
- https://github.com/Uniswap/smart-order-router
- https://github.com/Uniswap/smart-order-router/blob/main/src/routers/alpha-router/functions/best-swap-route.ts

## What afterSwap can and cannot do

The callback runs for the pool carrying that hook; it does not automatically observe or capture sells executed in unrelated existing pools.

Source inspection of v4-core indicates a hook can call `PoolManager.swap` during `afterSwap`, including against the same pool: `swap` requires the manager to be unlocked, which it remains during the callback. Calling `unlock` again instead fails with `AlreadyUnlocked`. `Hooks.sol` skips a hook's own before/after-swap callback when the swap caller is that hook, preventing that direct self-callback recursion. Nonzero currency deltas must be settled before the outer unlock finishes.

This is a **code-level feasibility finding, not a tested implementation**. Actual quote compatibility, caller/slippage protections, funding, settlement, gas, nested interactions, and failure behavior need fork tests. It does not establish that any arbitrary hook strategy will be accepted or routed by Labs. A normal post-swap callback does not grant unlimited authority to change the user's promised output; return-delta accounting is a separate feature.

Sources:
- https://github.com/Uniswap/v4-core/blob/main/src/PoolManager.sol
- https://github.com/Uniswap/v4-core/blob/main/src/libraries/Hooks.sol

## Economic accounting

Attracting flow requires competitive terms for sellers, while profitable JPYC accumulation requires a sufficiently low acquisition cost. These objectives constrain each other. If the strategy owns the LP and the hook, consolidate both holdings and cashflows: a cheap same-pool purchase by the hook is not pure incremental profit when the corresponding LP inventory change/loss belongs to the same owner. External arbitrage, fees, price movement, inventory risk, and later redemption should be separated. A hook does not make fiat JPY redemption atomic or create a guaranteed one-yen onchain exit.

## Polygon versus Avalanche

The official Smart Order Router's `V4_SUPPORTED` and `MIXED_SUPPORTED` include both Polygon and Avalanche. The current Trading API chain table lists normal swap support for Polygon but does **not** list Polygon for UniswapX. Avalanche is listed for UniswapX V3. Therefore a Polygon plan should not assume that implementing a UniswapX filler provides that chain's order flow.

Sources:
- https://github.com/Uniswap/smart-order-router/blob/main/src/util/chains.ts
- https://developers.uniswap.org/docs/trading/swapping-api/supported-chains

## Bounded next validation

Before any deployment, compare a static-fee, simple-afterSwap candidate against existing pools in a mainnet fork across representative sell sizes, including gas-adjusted quotes and consolidated LP-plus-hook P&L. No claim about attainable volume or yield is supported until those tests and live routing eligibility are verified.
