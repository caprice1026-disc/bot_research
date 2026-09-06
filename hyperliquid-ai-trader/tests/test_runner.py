from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import uuid

import pytest

from hyperliquid_ai_trader.agents import AgentDecisionError, DecisionEnvelope, ReviewEnvelope
from hyperliquid_ai_trader.config import Settings
from hyperliquid_ai_trader.exchange.base import (
    BracketResult,
    ExchangeAccountSnapshot,
    MarketObservation,
)
from hyperliquid_ai_trader.models import BookLevel, Candle, Side, TradeDecision
from hyperliquid_ai_trader.runner import LocalRunner, TradingService
from hyperliquid_ai_trader.storage import SQLiteStore
from hyperliquid_ai_trader.exchange.hyperliquid import make_cloids


def _settings() -> Settings:
    return Settings.from_mapping(
        {
            "HL_test_wallet": "0x" + "1" * 40,
            "HL_test_wallet_private_key": "0x" + "2" * 64,
            "GEMINI_API_KEY": "gemini-test-key",
            "EXECUTION_MODE": "dry_run",
        }
    )


def _store() -> SQLiteStore:
    directory = Path.cwd() / f"pytest-cache-files-runner-{uuid.uuid4().hex}"
    directory.mkdir()
    return SQLiteStore(directory / "trader.db")


def _market() -> MarketObservation:
    candles = [
        Candle(i * 60_000, 100 + i, 102 + i, 99 + i, 101 + i, 100 + (i % 2) * 10)
        for i in range(121)
    ]
    return MarketObservation(
        candles=candles,
        bids=[BookLevel(220.0, 2.0)],
        asks=[BookLevel(222.0, 1.0)],
        mark=Decimal("221"),
        oracle=Decimal("221"),
        funding=Decimal("0"),
        open_interest=Decimal("100"),
        size_decimals=3,
    )


class FakeExchange:
    def get_market_observation(self, coin: str, *, now_ms: int) -> MarketObservation:
        return _market()

    def get_account_snapshot(self, coin: str) -> ExchangeAccountSnapshot:
        return ExchangeAccountSnapshot(
            equity=Decimal("1000"),
            withdrawable=Decimal("1000"),
            position_size=Decimal("0"),
            entry_price=None,
            unrealized_pnl=Decimal("0"),
            open_orders=[],
        )

    def place_bracket(self, **kwargs) -> BracketResult:
        raise AssertionError("dry run must not place orders")

    def cancel_bot_orders(self, coin: str, cloids: list[str]) -> list[dict]:
        return []

    def close_position(self, coin: str) -> None:
        return None

    def get_user_fills(self, start_time_ms: int, end_time_ms: int) -> list[dict]:
        return [
            {
                "coin": "BTC",
                "tid": 77,
                "oid": 1,
                "dir": "Close Long",
                "sz": "0.005",
                "px": "222",
                "fee": "0.1",
                "closedPnl": "1.5",
                "time": end_time_ms - 1,
            }
        ]

    def get_user_funding(self, start_time_ms: int, end_time_ms: int) -> list[dict]:
        return [{"time": end_time_ms - 1, "hash": "fund-1", "delta": {"usdc": "0.2"}}]


class FixedAgent:
    model = "gemini-test"

    def decide(self, context: dict) -> DecisionEnvelope:
        return DecisionEnvelope(
            decision=TradeDecision(
                side=Side.LONG,
                stop_loss_pct=Decimal("0.5"),
                take_profit_pct=Decimal("1.0"),
                confidence=Decimal("0.7"),
                thesis="literal fixed decision",
                would_abstain=False,
                abstain_reason=None,
            ),
            prompt_hash="a" * 64,
            model=self.model,
        )


class FailingAgent:
    model = "gemini-test"

    def decide(self, context: dict) -> DecisionEnvelope:
        raise AgentDecisionError("rate_limited")


class ProtectionFailureExchange(FakeExchange):
    def __init__(self) -> None:
        self.place_calls = 0

    def place_bracket(self, **kwargs) -> BracketResult:
        self.place_calls += 1
        cloids = make_cloids(kwargs["run_id"], kwargs["slot"])
        return BracketResult(
            success=False,
            requires_recovery=True,
            error_type="protection_rejected",
            cloids=cloids,
            statuses=[{"filled": {"totalSz": "0.005", "oid": 1}}, "waitingForTrigger", {"error": "badTrigger"}],
            filled_size=Decimal("0.005"),
        )


class AbstainingAgent(FixedAgent):
    def decide(self, context: dict) -> DecisionEnvelope:
        envelope = super().decide(context)
        decision = envelope.decision
        return DecisionEnvelope(
            decision=TradeDecision(
                side=decision.side,
                stop_loss_pct=decision.stop_loss_pct,
                take_profit_pct=decision.take_profit_pct,
                confidence=decision.confidence,
                thesis=decision.thesis,
                would_abstain=True,
                abstain_reason="cost exceeds expected edge",
            ),
            prompt_hash=envelope.prompt_hash,
            model=envelope.model,
        )


class CapturingAgent(FixedAgent):
    def __init__(self) -> None:
        self.context: dict | None = None

    def decide(self, context: dict) -> DecisionEnvelope:
        self.context = context
        return super().decide(context)


def test_run_once_is_idempotent_and_records_simulated_decision() -> None:
    store = _store()
    service = TradingService(
        settings=_settings(),
        exchange=FakeExchange(),
        trader=FixedAgent(),
        reviewer=None,
        store=store,
        run_id="run-001",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    first = service.run_once(slot=0, scheduled_at_ms=1_000)
    duplicate = service.run_once(slot=0, scheduled_at_ms=1_000)

    assert first.status == "simulated"
    assert duplicate.status == "duplicate"
    assert store.get_cycle("run-001", 0)["status"] == "simulated"
    store.close()


def test_run_once_records_mandatory_exception_when_model_fails() -> None:
    store = _store()
    service = TradingService(
        settings=_settings(),
        exchange=FakeExchange(),
        trader=FailingAgent(),
        reviewer=None,
        store=store,
        run_id="run-002",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    result = service.run_once(slot=0, scheduled_at_ms=1_000)

    assert result.status == "mandatory_entry_exception"
    assert result.error_type == "rate_limited"
    assert store.get_cycle("run-002", 0)["error_type"] == "rate_limited"
    store.close()


def test_run_once_preserves_validated_decision_when_risk_rejects_it() -> None:
    class OutsideRangeAgent(FixedAgent):
        def decide(self, context: dict) -> DecisionEnvelope:
            envelope = super().decide(context)
            return DecisionEnvelope(
                decision=TradeDecision(
                    side=envelope.decision.side,
                    stop_loss_pct=Decimal("0.05"),
                    take_profit_pct=envelope.decision.take_profit_pct,
                    confidence=envelope.decision.confidence,
                    thesis=envelope.decision.thesis,
                    would_abstain=envelope.decision.would_abstain,
                    abstain_reason=envelope.decision.abstain_reason,
                ),
                prompt_hash=envelope.prompt_hash,
                model=envelope.model,
            )

    store = _store()
    service = TradingService(
        settings=_settings(),
        exchange=FakeExchange(),
        trader=OutsideRangeAgent(),
        reviewer=None,
        store=store,
        run_id="run-risk-decision",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    result = service.run_once(slot=0, scheduled_at_ms=1_000)

    assert result.error_type == "risk_rejected"
    decision = store.connection.execute(
        "SELECT arguments_json FROM decisions WHERE run_id='run-risk-decision'"
    ).fetchone()
    assert '"stop_loss_pct": "0.05"' in decision["arguments_json"]
    store.close()


def test_run_once_records_market_transport_failure_and_allows_scheduler_to_continue() -> None:
    class MarketFailureExchange(FakeExchange):
        calls = 0

        def get_market_observation(self, coin: str, *, now_ms: int) -> MarketObservation:
            self.calls += 1
            if self.calls > 1:
                raise TimeoutError("market request timed out")
            return super().get_market_observation(coin, now_ms=now_ms)

    store = _store()
    service = TradingService(
        settings=_settings(),
        exchange=MarketFailureExchange(),
        trader=FixedAgent(),
        reviewer=None,
        store=store,
        run_id="run-market-failure",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    result = service.run_once(slot=0, scheduled_at_ms=1_000)

    assert result.status == "mandatory_entry_exception"
    assert result.error_type == "market_data_error"
    store.close()


def test_run_once_skips_order_when_agent_abstains_and_entry_is_optional() -> None:
    settings = Settings.from_mapping({
        "HL_test_wallet": "0x" + "1" * 40,
        "HL_test_wallet_private_key": "0x" + "2" * 64,
        "GEMINI_API_KEY": "gemini-test-key",
        "EXECUTION_MODE": "testnet_live",
        "MANDATORY_ENTRY": "false",
    })

    class CountingExchange(FakeExchange):
        def __init__(self) -> None:
            self.place_calls = 0

        def place_bracket(self, **kwargs) -> BracketResult:
            self.place_calls += 1
            return BracketResult(
                success=True,
                requires_recovery=False,
                error_type=None,
                cloids={},
                statuses=[],
                filled_size=Decimal("0.005"),
            )

    store = _store()
    exchange = CountingExchange()
    service = TradingService(
        settings=settings,
        exchange=exchange,
        trader=AbstainingAgent(),
        reviewer=None,
        store=store,
        run_id="run-abstain",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    result = service.run_once(slot=0, scheduled_at_ms=1_000)

    assert result.status == "abstained"
    assert exchange.place_calls == 0
    cycle = store.get_cycle("run-abstain", 0)
    assert cycle["status"] == "abstained"
    assert cycle["decision"]["would_abstain"] is True
    store.close()


def test_trader_context_includes_fee_and_spread_cost_estimate() -> None:
    settings = Settings.from_mapping({
        "HL_test_wallet": "0x" + "1" * 40,
        "HL_test_wallet_private_key": "0x" + "2" * 64,
        "GEMINI_API_KEY": "gemini-test-key",
        "TAKER_FEE_PCT": "0.045",
    })
    store = _store()
    trader = CapturingAgent()
    service = TradingService(
        settings=settings,
        exchange=FakeExchange(),
        trader=trader,
        reviewer=None,
        store=store,
        run_id="run-cost-context",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)
    service.run_once(slot=0, scheduled_at_ms=1_000)

    costs = trader.context["costs"]
    assert costs["taker_fee_bps"] == pytest.approx(4.5)
    assert costs["spread_bps"] == pytest.approx(90.49773755656108)
    assert costs["estimated_round_trip_cost_bps"] == pytest.approx(99.49773755656108)
    store.close()


def test_execution_protection_failure_halts_future_entries() -> None:
    settings = _settings()
    settings = Settings.from_mapping({
        "HL_test_wallet": "0x" + "1" * 40,
        "HL_test_wallet_private_key": "0x" + "2" * 64,
        "GEMINI_API_KEY": "gemini-test-key",
        "EXECUTION_MODE": "testnet_live",
    })
    store = _store()
    exchange = ProtectionFailureExchange()
    service = TradingService(
        settings=settings,
        exchange=exchange,
        trader=FixedAgent(),
        reviewer=None,
        store=store,
        run_id="run-halt",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    first = service.run_once(slot=0, scheduled_at_ms=1_000)
    second = service.run_once(slot=1, scheduled_at_ms=301_000)

    assert first.error_type == "protection_rejected"
    assert second.status == "execution_halted"
    assert exchange.place_calls == 1
    store.close()


def test_finalize_syncs_confirmed_exchange_evidence_and_completes_run() -> None:
    store = _store()
    service = TradingService(
        settings=_settings(),
        exchange=FakeExchange(),
        trader=FixedAgent(),
        reviewer=None,
        store=store,
        run_id="run-final",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    assert service.finalize(now_ms=2_000) is True

    trades = store.recent_closed_trades("run-final", limit=10)
    assert trades[0]["closed_pnl"] == "1.5"
    run = store.connection.execute("SELECT status, final_equity FROM runs WHERE run_id='run-final'").fetchone()
    assert run["status"] == "completed"
    assert run["final_equity"] == "1000"
    funding = store.connection.execute(
        "SELECT amount FROM funding_payments WHERE run_id='run-final'"
    ).fetchone()
    assert funding["amount"] == "0.2"
    store.close()


def test_finalize_records_one_minute_candle_mfe_mae_for_last_episode() -> None:
    store = _store()
    service = TradingService(
        settings=_settings(),
        exchange=FakeExchange(),
        trader=FixedAgent(),
        reviewer=None,
        store=store,
        run_id="run-excursion",
        git_sha="abc",
    )
    service.initialize(now_ms=0)
    assert service.run_once(slot=0, scheduled_at_ms=0).status == "simulated"

    assert service.finalize(now_ms=300_000) is True

    metric = store.connection.execute(
        "SELECT mfe_pct, mae_pct, method FROM episode_metrics WHERE run_id='run-excursion'"
    ).fetchone()
    assert metric is not None
    assert Decimal(metric["mfe_pct"]) >= 0
    assert Decimal(metric["mae_pct"]) > 0
    assert metric["method"] == "1m_candle_estimate"
    store.close()


class ScheduleService:
    def __init__(self) -> None:
        self.cycles: list[int] = []
        self.reviews: list[int] = []
        self.cleanup_calls = 0

    def run_once(self, *, slot: int, scheduled_at_ms: int):
        self.cycles.append(slot)

    def review_once(self, *, review_index: int, review_cycle: int, now_ms: int):
        self.reviews.append(review_cycle)

    def cleanup(self) -> bool:
        self.cleanup_calls += 1
        return True


def test_accelerated_36_cycle_schedule_runs_six_reviews_and_cleanup() -> None:
    service = ScheduleService()
    runner = LocalRunner(
        service=service,
        cycles=36,
        interval_seconds=0,
        review_every_cycles=6,
        clock_ms=lambda: 1_000,
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    summary = runner.run()

    assert service.cycles == list(range(36))
    assert service.reviews == [6, 12, 18, 24, 30, 36]
    assert service.cleanup_calls == 6
    assert summary.cleanup_ok is True


def test_scheduler_waits_for_slot_boundary_before_cleanup_and_review() -> None:
    class VirtualTime:
        value = 0.0

        def sleep(self, seconds: float) -> None:
            self.value += seconds

    virtual = VirtualTime()

    class TimedService(ScheduleService):
        def __init__(self) -> None:
            super().__init__()
            self.cycle_times: list[float] = []
            self.review_times: list[float] = []
            self.cleanup_times: list[float] = []

        def run_once(self, *, slot: int, scheduled_at_ms: int):
            super().run_once(slot=slot, scheduled_at_ms=scheduled_at_ms)
            self.cycle_times.append(virtual.value)

        def review_once(self, *, review_index: int, review_cycle: int, now_ms: int):
            super().review_once(
                review_index=review_index,
                review_cycle=review_cycle,
                now_ms=now_ms,
            )
            self.review_times.append(virtual.value)

        def cleanup(self) -> bool:
            self.cleanup_times.append(virtual.value)
            return super().cleanup()

    service = TimedService()
    runner = LocalRunner(
        service=service,
        cycles=7,
        interval_seconds=5,
        review_every_cycles=6,
        clock_ms=lambda: int(virtual.value * 1000),
        monotonic=lambda: virtual.value,
        sleeper=virtual.sleep,
    )

    runner.run()

    assert service.cycle_times == [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    assert service.review_times == [30.0]
    assert service.cleanup_times == [30.0, 35.0]


def test_review_skips_when_no_new_closed_trade_exists() -> None:
    class CountingReviewer:
        model = "gemini-review"

        def __init__(self) -> None:
            self.calls = 0

        def review(self, *, strategy, closed_trades, review_cycle):
            self.calls += 1
            state = dict(strategy)
            state.update(
                {
                    "version": strategy["version"] + 1,
                    "parent_version": strategy["version"],
                    "last_review_cycle": review_cycle,
                }
            )
            return ReviewEnvelope(
                state=state,
                patch={"base_version": strategy["version"], "summary": "unchanged", "operations": []},
                prompt_hash="b" * 64,
                model=self.model,
            )

    store = _store()
    reviewer = CountingReviewer()
    service = TradingService(
        settings=_settings(),
        exchange=FakeExchange(),
        trader=FixedAgent(),
        reviewer=reviewer,
        store=store,
        run_id="run-review-skip",
        git_sha="abc",
    )
    service.initialize(now_ms=1_000)

    service.review_once(review_index=1, review_cycle=1, now_ms=2_000)
    service.review_once(review_index=2, review_cycle=2, now_ms=3_000)

    assert reviewer.calls == 1
    status = store.connection.execute(
        "SELECT status, error_type FROM reviews WHERE run_id='run-review-skip' AND review_index=2"
    ).fetchone()
    assert dict(status) == {"status": "skipped_no_new_trades", "error_type": None}
    version_count = store.connection.execute(
        "SELECT COUNT(*) FROM strategy_versions WHERE run_id='run-review-skip'"
    ).fetchone()[0]
    assert version_count == 1
    store.close()
