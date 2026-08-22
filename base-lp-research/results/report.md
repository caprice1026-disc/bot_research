# Base Uniswap v3 LP Research Report

- Status: `partial_backtest`
- Source: `json-rpc`
- Event filter: `Swap` only
- Dataset window requested: `2026-06-01T00:00:00Z` to `2026-06-08T00:00:00Z`
- Block window: `46741327` to `47043727`
- Current collection root: `runs/rpc-swap-7d`
- Current progress: `13182` Swap logs, next block `46765827`
- Strategy: `threshold_reset`
- Counterfactual: `C0`
- Fee precision: `P0`

## Collection Outcome

Dune取得はアカウント側datapoint上限で停止したため、今回の7日間pilotは公式Base RPCへ切り替えた。`eth_getLogs`にはUniswap v3 Swap topicをRPC側フィルタとして指定し、Mint/Burn/Collect等は要求していない。

公式RPCへの負荷を抑えるため、500 block単位、request間隔0.25秒、1回90秒のbounded runとした。49 chunk（13,182 Swap logs）を取得し、`runs/rpc-swap-7d/results/collection_checkpoint.json` に `next_block=46765827` を保存した。取得workerは停止済みで、既存の旧範囲データは上書きしていない。

## Research Status

ログ順序検証は成功した。取得済み範囲は2026-06-01 00:00:01 UTCから13:36:31 UTCまでの約13.6時間で、全7日間ではない。partialを明示的に許可した探索用backtestを実行したが、parameter sweepは未実行である。

## Exploratory Backtest

- Terminal value: `4379.32 USD`
- HODL terminal value: `4969.74 USD`
- HODL alpha: `-590.42 USD`
- Net return: `-10.70%`
- Fees: `81.09 USD`
- Costs: `30.00 USD`
- Gross fee / cost: `2.70x`
- Maximum drawdown: `559.97 USD`
- Time in range: `94.39%`
- Rebalances: `10`

この結果から、取得済み区間では価格変動によるHODL劣後が大きく、Swap fee収入は発生したが、C0/P0モデル上で損失を相殺できなかったと読める。ただし、13.6時間のpartial区間のみなので、7日間の結論や戦略優位性を示すものではない。

## Limitations

このv0.1研究はC0およびP0近似を使用する。partial dataから投資判断、実運用損益、裁定可能性を結論づけない。
