# JPYC atomic route feasibility
Observed 2026-10-20 around 16:03–16:06 UTC. Research only; no trades or deployment.

## Conclusion
Fully on-chain arbitrage is possible if a same-chain route returns more of the borrowed asset than principal + loan premium + gas/MEV costs. Bank redemption at JPY 1 cannot be an atomic transaction leg. Equal JPY pegs do not create an executable 1:1 conversion.

The strongest topology candidate found is Polygon native USDC -> current JPYC at one pool -> native USDC at another pool. Current JPYC is 0xe7c3d8c9a439fede00d2600032d5db0be71c3c29; native USDC is 0x3c499c542cef5e3811e1192ce70d8cc03d5c3359. GeckoTerminal lists four active Uniswap v4 pools for that exact pair. This establishes candidate venues, NOT executable positive profit, usable tick liquidity, hook safety, or verified fees. Aggregate TVL and volume are not execution quotes. Current fixed-block round-trip quotes and all pool keys/hooks must be checked next.

## Polygon exact-pair pools, GeckoTerminal observation
- 0x4643e3152a6ea3957e087cefbba74a1c628aa643e6b8a0edfae23a5796dde012: TVL $701388.48, 24h volume $478860.28; display fee 0.05%.
- 0x7d4af4921182a9cb3f69aac24d0098a1a552d30f8a412c3d15e33e92eac0adf7: TVL $23770.45, volume $147988.80; display fee 0.03%.
- 0xadbf1264003c700f82c1f641ee795cbab3ca2c13410e354bfff509ba4a343ab9: TVL $21616.58, volume $46266.26; fee not in display name.
- 0x1749a01d0947a72866d1c8c0b7c695fba04d396504cfef4265ba69147f84ee79: TVL $21409.65, volume $25580.57; display fee 0.05%.
All four are Uniswap v4, not necessarily different DEX protocols. Additional v3 pool 0xfda7e76f768ce8152bbe6e2acbf5bf16faf02eb6 has TVL $27788.28 but only $0.089 volume/24h and display fee 0.3%. QuickSwap v3 0xbda506776accb792a20ae1464edbf009d912d4fe uses USDC.e instead of native USDC and TVL only $666.09; extra collateral conversion needed.
Source: https://api.geckoterminal.com/api/v2/networks/polygon_pos/tokens/0xe7c3d8c9a439fede00d2600032d5db0be71c3c29/pools?page=1

## Other JPY stable assets
### Jarvis jJPY
Official docs list Polygon Synthereum mint/redeem pool 0xAEc757BF73cc1f4609a1459205835Dd40b4e3F29 with USDC.e collateral and 0.3% fee. This is a synthetic asset with oracle/collateral/protocol risks, not a claim that every jJPY is redeemable to a Japanese bank at one yen. Live protocol redemption quote, pause/capacity/oracle freshness still unverified.
Sources: https://learn.jarvis.network/protocol-overview/minting/polygon and https://learn.jarvis.network/protocol-overview/minting

Curve official API identifies jJPY token 0x8343091F2499FD4b6174A46D067A920a3b851FF9. Existing 2jpy-2 pool 0xaA91CDD7abb47F821Cf07a2d38Cc8668DEAf1bdc pairs it with OLD prepaid JPYC 0x431D5dfF03120AFA4bDf332c61A6e1766eF37BDB, TVL $3130.62. Earlier 2jpy pool 0xE8dCeA7Fb2Baf7a9F4d9af608F06d78a687F8d9A pairs it with 0x6AE7Dfc73E0dDE2aa99ac063DcF7e8A63265108c, TVL $8592.57. Neither is the current redeemable JPYC.
Curve GYEN/JPYC/jJPY pool 0x1B33488CEa0087452b13b24318fc8d7bd0ed441f also uses OLD JPYC and reports TVL zero. Do not treat ticker names as contract identity.
Source: https://api.curve.finance/api/getPools/polygon/factory
Historic Jarvis first-party PLAAS example discusses JPYC/jJPY routes but predates current JPYC: https://medium.com/jarvis-network/plaas-1-4-synergies-with-fiat-backed-stablecoins-for-bridging-the-two-worlds-2c9997a26f64
No current-JPYC/jJPY direct pool verified in searched data. Absence from these sources is not proof no pool exists anywhere.

### GYEN
Issuer official wind-down notice says initial redemption deadline 2026-11-11. Ethereum contract 0xC08512927D12348F6620a698105e1BAac6EcD911; official notice also lists Arbitrum GYEN 0x589d35656641d6aB57A545F08cf473eCD9B6D5F7. Long-lived strategy should not assume continuing liquidity or redemption. Issuer redemption is off-chain and cannot finish a flash-loan cycle.
Ethereum GYEN/USDC largest listed pool 0x31f878cb3b1d6c424e8eea4b1980bede0eb3c20306ef289a22bd39702b564e3c, reported TVL $904528.50 but only $78.15 volume/24h. Current JPYC has an Ethereum USDC pool, but two separate USD/JPY markets alone do not connect JPYC to GYEN. A direct executable conversion edge is missing in verified sources.
Sources:
https://support.stablecoin.z.com/hc/en-us/articles/60491926674585-GYEN-and-ZUSD-Wind-down-How-to-Redeem-Before-the-November-11-2026-Deadline
https://support.stablecoin.z.com/hc/en-us/articles/360053460793-What-are-the-contract-addresses-for-GYEN-and-ZUSD
https://api.geckoterminal.com/api/v2/networks/eth/tokens/0xc08512927d12348f6620a698105e1baac6ecd911/pools?page=1

## Flash liquidity and profit
Aave premium is governance-variable: docs initial 0.05% is NOT a verified current Polygon fee. Polygon pool from official address book: 0x794a61358D6845594F94dc1DB02A252b5b4814aD. Attempted FLASHLOAN_PREMIUM_TOTAL() and Jarvis syntheticToken() using Tenderly public RPC; both HTTP 403. Stopped without alternate RPC bypass. No current fee claimed.
https://aave.com/docs/aave-v3/guides/flash-loans
https://raw.githubusercontent.com/bgd-labs/aave-address-book/main/src/AaveV3Polygon.sol

Formula: final borrowed-token output minus principal minus flash premium minus gas equivalent minus tips/MEV costs. Swap fees/impact should already be included in actual sequential quotes, not counted twice. Reverting can protect principal but gas is still lost. Searching off-chain then submitting a conditional contract is compatible with entirely on-chain atomic settlement; contracts do not wake themselves to observe sales. Flash liquidity supplies temporary capital, not profit. Uniswap v4 flash accounting may avoid an external lender for suitable settlement flows, but still requires all currency deltas settled before unlock completes. Cross-chain bridges and bank redemption cannot be ordinary single-chain atomic repayment legs.
