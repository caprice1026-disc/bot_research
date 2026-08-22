from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import uuid

import pytest

from hyperliquid_ai_trader.config import Settings
from hyperliquid_ai_trader.exchange.base import (
    BracketResult,
    ExchangeAccountSnapshot,
    MarketObservation,
)
from hyperliquid_ai_trader.models import BookLevel, Candle
from hyperliquid_ai_trader.risk import RiskEngine
from hyperliquid_ai_trader.storage import SQLiteStore
from hyperliquid_ai_trader.trading_tools import ToolRejected, TradingTools


def _settings(mode: str = "testnet_live") -> Settings:
    return Settings.from_mapping(
        {
            "HL_test_wallet": "0x" + "1" * 40,
            "HL_test_wallet_private_key": "0x" + "2" * 64,
            "GEMINI_API_KEY": "gemini-test-key",
            "EXECUTION_MODE": mode,
        }
    )


def _database() -> Path:
    directory = Path.cwd() / f"pytest-cache-files-tools-{uuid.uuid4().hex}"
    directory.mkdir()
    return directory / "trader.db"


def _observation() -> MarketObservation:
    candles = [
        Candle(i * 60_000, 50_000.0, 50_010.0, 49_990.0, 50_000.0, 100.0)
        for i in range(121)
    ]
    return MarketObservation(
        candles=candles,
        bids=[BookLevel(49_999.0, 2.0)],
        asks=[BookLevel(50_001.0, 1.0)],
        mark=Decimal("50000"),
        oracle=Decimal("50000"),
        funding=Decimal("0"),
        open_interest=Decimal("100"),
        size_decimals=3,
    )


class FakeTradingExchange:
    def __init__(self, result: BracketResult) -> None:
        self.result = result
        self.place_calls = 0
        self.close_calls = 0
        self.cancel_calls = 0

    def get_market_observation(self, coin: str, *, now_ms: int) -> MarketObservation:
        return _observation()

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
        self.place_calls += 1
        return self.result

    def cancel_bot_orders(self, coin: str, cloids: list[str]) -> list[dict]:
        self.cancel_calls += 1
        return [{"status": "ok"}]

    def close_position(self, coin: str) -> dict:
        self.close_calls += 1
        return {"status": "ok"}


class LostResponseExchange(FakeTradingExchange):
    def place_bracket(self, **kwargs) -> BracketResult:
        self.place_calls += 1
        raise TimeoutError("response was lost after submission")


def _store() -> SQLiteStore:
    store = SQLiteStore(_database())
    store.create_run(
        run_id="run-tools",
        mode="testnet_live",
        started_at_ms=1,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc",
    )
    assert store.reserve_cycle("run-tools", 1, scheduled_at_ms=1, strategy_version=1)
    return store


def _success_result() -> BracketResult:
    return BracketResult(
        success=True,
        requires_recovery=False,
        error_type=None,
        cloids={
            "entry": "0x" + "1" * 32,
            "tp": "0x" + "2" * 32,
            "sl": "0x" + "3" * 32,
        },
        statuses=[
            {"filled": {"oid": 1}},
            {"resting": {"oid": 2}},
            {"resting": {"oid": 3}},
        ],
        filled_size=Decimal("0.005"),
    )


def test_tool_places_validated_bracket_once_and_records_all_cloids() -> None:
    store = _store()
    exchange = FakeTradingExchange(_success_result())
    tools = TradingTools(
        exchange=exchange,
        risk_engine=RiskEngine(_settings()),
        store=store,
        settings=_settings(),
        run_id="run-tools",
        slot=1,
        day_start_equity=Decimal("1000"),
        session_peak_equity=Decimal("1000"),
        daily_realized_pnl=Decimal("0"),
        now_ms=10,
    )

    result = tools.open_position(
        side="long",
        stop_loss_pct=0.5,
        take_profit_pct=1.0,
        confidence=0.8,
        thesis="literal test thesis",
        would_abstain=False,
        abstain_reason=None,
    )

    assert result.success is True
    assert exchange.place_calls == 1
    assert len(store.known_cloids("run-tools")) == 3
    with pytest.raises(ToolRejected, match="already used"):
        tools.open_position(
            side="short",
            stop_loss_pct=0.5,
            take_profit_pct=1.0,
            confidence=0.8,
            thesis="second call",
            would_abstain=False,
            abstain_reason=None,
        )
    store.close()


def test_tool_recovers_partial_fill_by_canceling_and_closing() -> None:
    partial = _success_result()
    partial = BracketResult(
        success=False,
        requires_recovery=True,
        error_type="partial_fill",
        cloids=partial.cloids,
        statuses=partial.statuses,
        filled_size=Decimal("0.004"),
    )
    store = _store()
    exchange = FakeTradingExchange(partial)
    tools = TradingTools(
        exchange=exchange,
        risk_engine=RiskEngine(_settings()),
        store=store,
        settings=_settings(),
        run_id="run-tools",
        slot=1,
        day_start_equity=Decimal("1000"),
        session_peak_equity=Decimal("1000"),
        daily_realized_pnl=Decimal("0"),
        now_ms=10,
    )

    result = tools.open_position(
        side="long",
        stop_loss_pct=0.5,
        take_profit_pct=1.0,
        confidence=0.8,
        thesis="literal test thesis",
        would_abstain=False,
        abstain_reason=None,
    )

    assert result.success is False
    assert result.recovered is True
    assert exchange.cancel_calls == 1
    assert exchange.close_calls == 1
    store.close()


def test_dry_run_never_calls_exchange_order_method() -> None:
    store = _store()
    exchange = FakeTradingExchange(_success_result())
    settings = _settings("dry_run")
    tools = TradingTools(
        exchange=exchange,
        risk_engine=RiskEngine(settings),
        store=store,
        settings=settings,
        run_id="run-tools",
        slot=1,
        day_start_equity=Decimal("1000"),
        session_peak_equity=Decimal("1000"),
        daily_realized_pnl=Decimal("0"),
        now_ms=10,
    )

    result = tools.open_position(
        side="short",
        stop_loss_pct=0.5,
        take_profit_pct=1.0,
        confidence=0.8,
        thesis="dry run",
        would_abstain=False,
        abstain_reason=None,
    )

    assert result.success is True
    assert result.simulated is True
    assert exchange.place_calls == 0
    store.close()


def test_tool_treats_lost_exchange_response_as_ambiguous_and_runs_recovery() -> None:
    store = _store()
    exchange = LostResponseExchange(_success_result())
    tools = TradingTools(
        exchange=exchange,
        risk_engine=RiskEngine(_settings()),
        store=store,
        settings=_settings(),
        run_id="run-tools",
        slot=1,
        day_start_equity=Decimal("1000"),
        session_peak_equity=Decimal("1000"),
        daily_realized_pnl=Decimal("0"),
        now_ms=10,
    )

    result = tools.open_position(
        side="long",
        stop_loss_pct=0.5,
        take_profit_pct=1.0,
        confidence=0.8,
        thesis="lost response",
        would_abstain=False,
        abstain_reason=None,
    )

    assert result.success is False
    assert result.error_type == "ambiguous_response"
    assert result.recovered is True
    assert exchange.place_calls == 1
    assert exchange.cancel_calls == 1
    assert exchange.close_calls == 1
    assert len(store.known_cloids("run-tools")) == 3
    store.close()
