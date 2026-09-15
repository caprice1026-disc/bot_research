# Initial strategy provenance

`initial_strategy.json` version 1 was fixed from an offline Binance USD-M
BTCUSDT 1-minute analysis. It does not claim a Hyperliquid result and it does
not contain a model response, account state, or fabricated trade count.

- Input period: 2025-09-01 through 2026-08-31 UTC (365 days).
- Input hashes: normalized candles
  `1832caee2dc69ff72f8545e0588f3e9de26da811478bd8ecc535f81b8e301dc6`; funding
  `d87d8171e101d0d9259126bae0752ec750fcac6eb8870903eff660a3009cd67a`.
- Method: no LLM calls. Always abstain, five-minute momentum, and five-minute
  mean reversion used the fixed 1-second delay, 300-second hold, fees, spread,
  slippage, Funding, and ResearchRiskEngine from
  `binance_development.json`.
- Continuous-account replay stopped new entries at the 25% maximum drawdown:
  momentum made 749 trades (net -250.2257446343554018754308013 USD) and mean
  reversion made 796 (net -249.6710611436074911546906322 USD). The final
  unavailable price suffix remains `partial`, not a zero-return observation.
- Independent UTC-month replays were used only for regime coverage, not summed
  as an annual capital curve. All 12 months reached the same drawdown limit.
  Across those independent windows, average net PnL per trade was
  -0.3270316492468831662865819788 USD (momentum) and
  -0.3202367196862263713277238037 USD (mean reversion).

The reproducible local analysis artifact is
`data/research/binance-baseline-analysis-2025-09-01_2026-08-31.json`; generated
market data and research output remain untracked.
