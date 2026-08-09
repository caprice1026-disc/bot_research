# Base Uniswap v3 LP Research Report

- Status: `partial`
- Source: `json-rpc`
- Event filter: `Swap` only
- Dataset window requested: `2026-06-01T00:00:00Z` to `2026-06-08T00:00:00Z`
- Block window: `46741327` to `47043727`
- Current collection root: `runs/rpc-swap-7d`
- Current progress: `1042` Swap logs, next block `46742827`
- Strategy: `threshold_reset`
- Counterfactual: `C0`
- Fee precision: `P0`

## Collection Outcome

Dune取得はアカウント側datapoint上限で停止したため、今回の7日間pilotは公式Base RPCへ切り替えた。`eth_getLogs`にはUniswap v3 Swap topicをRPC側フィルタとして指定し、Mint/Burn/Collect等は要求していない。

公式RPCへの負荷を抑えるため、500 block単位、request間隔0.25秒、1回90秒のbounded runとした。初回と再開runで3 chunk（1,042 Swap logs）を取得し、`runs/rpc-swap-7d/results/collection_checkpoint.json` に `next_block=46742827` を保存した。既存の旧範囲データは上書きせず、今回のpilotは別rootへ分離している。

## Research Status

データセットはまだ`partial`であり、全7日間の検証、Parquet化、backtest、parameter sweepは実行していない。収集完了後に同じrootで`validate-data`、`backtest`、`sweep`を実行する。

## Limitations

このv0.1研究はC0およびP0近似を使用する。partial dataから投資判断、実運用損益、裁定可能性を結論づけない。
