# Research v3 Issues #8-#16 Design

## Goal

Complete the open research-v3 issues for the Binance input path and the Hyperliquid research path while preserving honest `insufficient_data` and partial states. The result must make every artifact traceable to the exact input and configuration that produced it, use one execution-cost definition throughout simulation and prompts, and keep replay, review, forward comparison, and Testnet execution audit separate.

## Scope and constraints

The existing research path under `hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/` remains the integration boundary. Existing live/Testnet code is not silently reused for research semantics. SQLite, JSONL, `Decimal`, and the existing public SDK reader are preferred; no new service or framework is introduced. Paid API calls remain disabled unless a config explicitly enables them with a positive budget, and a submission whose provider outcome is unknown is never automatically resent.

Issue #8 is implemented first because Funding and source provenance are upstream inputs. Issue #9 then adds a shared artifact verifier. Issue #10 fixes simulator semantics and exposes the common cost model. Issues #11 and #12 extend immutable request identity and add the provider-independent Batch state machine. Issue #13 adds sequential Replay and risk/account state. Issue #14 adds UTC-boundary Reviewer policy. Issue #15 freezes the complete response-affecting configuration and runs isolated Static/Adaptive accounts. Issue #16 consumes only that freeze contract for fixture and optional Testnet audit.

## Architecture

`binance-btcusdt-futures-research` produces verified CSVs and a manifest. The Hyperliquid package imports those CSVs into normalized candles and carries a source hash through point, request, response, evaluation, replay, and freeze artifacts. `research/artifacts.py` owns canonical JSON and hash-chain checks. `research/simulator.py` owns execution prices, fees, spread, slippage, SL/TP basis, and funding attachment. `research/risk.py` and `research/replay.py` own account state and sequential decisions. Reviewer and Forward use pure policy/state helpers so they can be tested without an LLM or exchange. `testnet_audit.py` is an execution-audit adapter, not a profitability evaluator.

Every stage rejects mismatched parent hashes, experiment IDs, market identity, or response-affecting settings before producing downstream output. Missing data is recorded as missing; it is never converted to zero funding, zero trades, or a successful result.

## Data flow

1. Binance archive checksums and normalized CSV provenance are verified.
2. Normalized candles are imported and hashed.
3. Points are selected and their features are recomputed from the exact candle artifact.
4. Prepared requests contain exact `as_of_ms`, market identity, execution settings, strategy, model, and generation settings.
5. Responses are validated one-to-one against requests and produce raw/offset/adjusted confidence fields without changing `would_abstain`.
6. Evaluation and Replay use the same execution cost model; Replay alone mutates accounts.
7. Reviewer consumes only closed, pre-cutoff evidence and emits a validated patch that is either applied at the next UTC boundary or held.
8. Freeze records every response-affecting hash and rejects drift. Forward runs Static and Adaptive on identical snapshots with separate accounts.
9. Testnet fixture and frozen-model modes record execution timelines and recovery evidence; Testnet PnL is not a research result.

## Failure and safety semantics

Funding gaps, unavailable candle windows, invalid artifacts, provider failures, missed slots, risk rejections, and Trader abstention have distinct statuses. A late Reviewer patch is held, not applied intraday. A failed review does not consume evidence. A Batch timeout becomes `submission_unknown` and requires an explicit sync result. Protection failure in the audit path cancels bot orders, emergency-closes, confirms flat, and confirms no orphan bot orders.

## Verification

Each issue gets focused regression tests before implementation. The final checks are the Binance and Hyperliquid test suites, `ruff`, `pyright`, `git diff --check`, a fixture run, and a remote SHA check after pushing `main`. Real data is fetched or reverified only when the required public network is available; otherwise the artifact remains explicitly partial or insufficient.

