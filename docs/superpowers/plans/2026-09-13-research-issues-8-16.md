# Research v3 Issues #8-#16 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the upstream Binance provenance path and complete the isolated Hyperliquid research-v3 pipeline through frozen Forward and Testnet execution audit.

**Architecture:** Reuse the existing JSONL/SQLite/CLI design. Add one artifact verifier for hash lineage, one cost model shared by Simulator and request preparation, and small pure state machines for Replay, Reviewer, Forward, Batch, and audit recovery. Keep live trading code separate.

**Tech Stack:** Python 3.13, standard library `Decimal`/`hashlib`/`sqlite3`/`json`, existing pytest/ruff/pyright, existing public Hyperliquid SDK.

## Global Constraints

- Never turn missing data or an incomplete holding window into a successful zero result.
- Never automatically resend `submission_unknown` provider requests.
- Use `entry_time_ms < funding_time_ms <= exit_time_ms` for funding eligibility.
- Use execution price as the SL/TP basis and apply spread/slippage exactly once.
- Do not change the existing live/Testnet input contract while adding research paths.
- Stage and push only files belonging to this work.

### Task 1: Binance Funding coverage and CSV provenance (#8)

**Files:**
- Modify: `binance-btcusdt-futures-research/src/btc_regime_eda/collection.py`
- Modify: `binance-btcusdt-futures-research/download_binance_klines.py`
- Modify: `binance-btcusdt-futures-research/tests/test_download_binance_klines.py`
- Create: `binance-btcusdt-futures-research/tests/test_collection.py`

- [ ] Add a pure month-range helper and tests for partial-month inclusion and month-start exclusion.
- [ ] Change Funding collection to use the helper, preserve 404s in `missing_periods`, and mark the dataset incomplete instead of filling zeroes.
- [ ] Add output SHA, row count, first/last open time, and source archive hashes to the fetch manifest.
- [ ] Make `--verify-only` require and validate the manifest, requested end date, CSV hash, shape, semantic OHLC/trade/volume rules, and archive hashes.
- [ ] Add safe temporary output replacement and source checksum comparison without overwriting validated raw files.
- [ ] Run focused tests and the complete Binance test suite.

### Task 2: Artifact lineage and exact point features (#9)

**Files:**
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/artifacts.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/cli.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/points.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/preparation.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/responses.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/evaluation.py`
- Test: `hyperliquid-ai-trader/tests/test_research_artifacts.py`

- [ ] Add `sha256_path`, manifest lookup/loading, artifact verification, parent-hash verification, canonical config hashing, and experiment fingerprint helpers.
- [ ] Have each CLI stage verify its parent manifest before reading it and persist the inherited lineage.
- [ ] Rebuild each selected point's features from the supplied candle series and reject changed prices/features even when timestamps match.
- [ ] Add focused tests for tampering, parent mismatch, fingerprint changes, and exact-original acceptance.
- [ ] Run the focused and existing Hyperliquid research tests.

### Task 3: One Simulator cost/funding contract (#10)

**Files:**
- Create or modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/costs.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/simulator.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/binance.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/config.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/preparation.py`
- Test: `hyperliquid-ai-trader/tests/test_research_costs.py`

- [ ] Add explicit `sl_tp_basis` and reject unsupported values.
- [ ] Base trigger levels on executed entry price and preserve gap/intrabar policy.
- [ ] Centralize entry/exit execution, fee, spread, slippage, and round-trip estimate calculations.
- [ ] Apply Funding only for strictly-after-entry and at-or-before-exit scheduled events; missing required events remain incomplete.
- [ ] Pass the shared estimate and version into prepared requests and include them in request identity.
- [ ] Add tests for long/short trigger levels, no double charge, funding boundaries, missing events, and prompt/simulator cost equality.

### Task 4: Trader v2 identity and Batch lifecycle (#11/#12)

**Files:**
- Create: `hyperliquid-ai-trader/prompts/research/trader_v002.md`
- Modify: `hyperliquid-ai-trader/prompts/research/constitution.md`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/request_identity.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/preparation.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/responses.py`
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/batch.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/store.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/cli.py`
- Test: `hyperliquid-ai-trader/tests/test_research_batch.py`

- [ ] Require exact market `as_of_ms == decision_time_ms` and response-affecting config identity.
- [ ] Exclude `confidence_calibration` from Trader input and persist raw, offset, and adjusted confidence once.
- [ ] Extend SQLite request records with job, timing, usage, cost, response, and error fields through an additive migration.
- [ ] Implement provider-neutral submit/sync interfaces: reserve budget before provider calls, mark unknown on uncertain submission, and never auto-resend unknown requests.
- [ ] Reuse the first 50 only when the full request identity matches; submit exactly the missing suffix for expansion.
- [ ] Test default paid-API refusal, budget reservation, timeout state, sync resolution, reuse, and partial completion.

### Task 5: Sequential Replay and Research Risk (#13)

**Files:**
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/risk.py`
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/replay.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/simulator.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/config.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/store.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/cli.py`
- Test: `hyperliquid-ai-trader/tests/test_research_replay.py`

- [ ] Add risk-per-trade, daily-loss, drawdown, notional, leverage, and minimum-notional settings with validation.
- [ ] Implement pure risk decisions for existing position, daily loss, drawdown, margin capacity, and size from current equity.
- [ ] Add `VirtualAccount.advance_time(timestamp_ms)` and reset UTC daily state independently of trade close.
- [ ] Process saved decisions strictly once in timestamp order, recording risk rejection, abstention, model failure, shadow, and trade separately.
- [ ] Persist replay progress/account updates idempotently and add the three-day CLI path.
- [ ] Test ordering, limits, day reset, shadow isolation, max hold, restart, and deterministic rerun.

### Task 6: UTC Reviewer and strategy patch policy (#14)

**Files:**
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/reviewer_evidence.py`
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/strategy_policy.py`
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/reviewer.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/replay.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/store.py`
- Create: `hyperliquid-ai-trader/prompts/research/reviewer_v002.md`
- Test: `hyperliquid-ai-trader/tests/test_research_reviewer.py`

- [ ] Build evidence only from closed, same-experiment, pre-cutoff records with explicit trade/shadow/failure classification.
- [ ] Validate patch sizes, operation count, evidence IDs, direction counts, offset total, and daily delta in Python.
- [ ] Apply only validated patches at the next `00:05` boundary; hold `00:05:00+` results and revalidate parent version next day.
- [ ] Keep old strategy on failed/invalid/insufficient reviews and consume evidence only for validated no-change or patch outcomes.
- [ ] Apply confidence offset once for stored adjusted values without changing `would_abstain`.
- [ ] Test evidence rejection, thresholds, held patches, intraday immutability, and exact-once calibration.

### Task 7: Freeze and Static/Adaptive Forward (#15)

**Files:**
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/freeze.py`
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/forward.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/cli.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/store.py`
- Test: `hyperliquid-ai-trader/tests/test_research_forward.py`

- [ ] Create a freeze manifest containing all response-affecting hashes, execution/risk/cost semantics, code identity, dates, budgets, thresholds, and stop rules.
- [ ] Reject any resume/start when the same experiment's fingerprint differs.
- [ ] Feed identical received snapshots and decision slots to Static and Adaptive with separate accounts; only Adaptive can apply validated daily patches.
- [ ] Mark delayed slots missed and never backfill them; persist slot/account/patch idempotently.
- [ ] Reject model fallback or future/unconfirmed candle use during frozen Forward.
- [ ] Add CLI and focused tests for every drift and restart rule.

### Task 8: Frozen fixture/Testnet execution audit (#16)

**Files:**
- Create: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/testnet_audit.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/cli.py`
- Modify: `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/store.py`
- Test: `hyperliquid-ai-trader/tests/test_research_testnet_audit.py`

- [ ] Implement fixture mode with deterministic LONG/SHORT decisions for entry, partial fill, TP, SL, cancel, timeout, emergency close, and orphan cleanup.
- [ ] Implement frozen-model mode that loads only the freeze manifest's candles, prompt, strategy, model, and generation settings.
- [ ] Record the complete execution timeline plus actual filled size and `avgPx`; reconcile stop risk against actual fill.
- [ ] Size against worst allowed entry price, resize protection to partial fills, and execute recovery through cancel, emergency close, flat confirmation, and orphan check.
- [ ] Base max-hold deadline on actual entry fill and restore pending deadlines on restart.
- [ ] Test each recovery and timing condition without treating Testnet PnL as profitability evidence.

### Task 9: Final verification and push

**Files:**
- Modify: `.agent/2026-09-10-hyperliquid-research-v3.md`

- [ ] Run both project test suites with dedicated temporary directories if needed, then `ruff`, `pyright`, fixture commands, and `git diff --check`.
- [ ] Inspect all manifests and confirm incomplete/partial data is labeled honestly.
- [ ] Update the living ExecPlan with evidence, decisions, outcomes, and remaining external limitations.
- [ ] Stage only intended files, commit, push with `git push origin main`, and confirm `git ls-remote origin refs/heads/main` equals `git rev-parse HEAD`.
