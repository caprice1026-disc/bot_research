# Read-only historical event replay inputs

Collected from the free public Tenderly Polygon endpoint on 2026-10-20. No credentials, paid access, wallet, trading, redemption, or authentication were used.

## Provenance and coverage

- Initial state is after block 95802902 (2026-10-19 11:35:00 UTC).
- Initial slot0 and active liquidity: ../public_rpc/grid/quotes_raw.json, key 95802902.
- Scanned every block from 95802903 through 95859621 inclusive in 12 contiguous, non-overlapping chunks of at most 5000 blocks.
- PoolManager address 0x67366782805870060151383f4bbff9dab53e5cd6; topics filter [null, 0x4643e3152a6ea3957e087cefbba74a1c628aa643e6b8a0edfae23a5796dde012]. Thus all event kinds indexed by this pool ID were retrieved, not only swaps.
- 149 logs: 120 Swap, 29 ModifyLiquidity. No Donate, ProtocolFeeUpdated or unknown pool events were observed.
- Event order is block number, transaction index, log index. All 146 unique event block hashes match the independently fetched block headers. No duplicate logs.
- Raw logs, RPC responses, block headers, decoded events, and validation are retained. Initial probe_second_results.json is a separate pre-window probe, not included in replay inputs.

## Bounded initial tick state

- Initial bitmap words 126, 127 and 128 were fetched. This is NOT the entire global tick space.
- The full known bitmap region is tick 322560 through 330239 (initialized ticks at spacing 10 up to 330230).
- All 108 initialized ticks in that region have liquidityGross and signed liquidityNet fetched at the initial block.
- The observed five-minute state ticks were 326997 through 327017. Nonetheless a counterfactual simulation must stop if it needs unknown state outside the fetched region. Do not assume outside ticks have no liquidity.
- Starting active liquidity anchors the bounded state; summing net liquidity over only the bounded region does not have to yield zero.

## Conditional replay interpretation

Replaying the observed negative token delta as the input amount and direction is a fixed-observed-input hypothesis. Logs do not identify whether the original instruction was exact-input or exact-output, its original price limit, its unfilled amount, or surrounding multi-hop route intent. The original outcome gives consumed input only. First validate a no-own-trade replay against observed sqrtPriceX96, tick and active liquidity after every swap. Tiny integer-rounding discrepancies may occur, and must be measured rather than silently overwritten.

After hypothetical own swaps, retain hypothetical state. Reapply external observed swap inputs and observed liquidity deltas in order; do not reset price to observed post-swap snapshots. This assumes external traders/LPs do not change their orders in response to our trades. Actual competitors, routing, MEV, slippage constraints, and LP funding availability may change, so output is conditional simulation, not a reconstructed achievable real-world profit.

The pool has no hook. The observed protocol fee is packed 0x7d07d, LP fee 500 pips, total swap fee 625 pips (0.0625%). Avoid subtracting it twice. Funding/remittance costs are zero by user instruction; transaction gas assumptions remain separate. Finite capital and non-instant redemption should be explicit.

## Sources

https://docs.polygon.technology/pos/reference/rpc-endpoints
https://developers.uniswap.org/docs/protocols/v4/deployments
https://raw.githubusercontent.com/Uniswap/v4-core/main/src/interfaces/IPoolManager.sol
https://raw.githubusercontent.com/Uniswap/v4-core/main/src/interfaces/IProtocolFees.sol
https://raw.githubusercontent.com/Uniswap/v4-periphery/main/src/lens/StateView.sol
https://raw.githubusercontent.com/Uniswap/v4-core/main/src/libraries/ProtocolFeeLibrary.sol
