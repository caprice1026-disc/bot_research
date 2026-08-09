# Base Uniswap v3 LP Research Report

- Status: `collection_error`
- Source: `dune-sql`
- Dataset window requested: `2026-06-01T00:00:00Z` to `2026-06-08T00:00:00Z`
- Dune execution ID: `01KZJDXXQWZ98Z7B5K535TKAPH`
- Strategy: `threshold_reset`
- Counterfactual: `C0`
- Fee precision: `P0`

## Collection Outcome

The Dune SQL execution completed, but Dune rejected the first results page with HTTP 402 because this API key would exceed its configured datapoint limit for the billing cycle. No raw or Parquet dataset was treated as complete, and no backtest or parameter sweep was run from this failed collection.

An earlier one-month query execution (`01KZJDKMAPTTEN9AEHA6M9FEAJ`) reported 759,888 Swap rows. The seven-day request was chosen to reduce result volume, but the account-level limit was already exhausted.

## Next Requirement

Increase or reset the Dune API key's datapoint limit, then rerun `collect-dune` without `--fresh`. The saved execution ID will be reused; no new SQL execution is required for the seven-day query.

## Limitations

This v0.1 research uses C0 and P0 approximations. It is not an execution or investment recommendation.
