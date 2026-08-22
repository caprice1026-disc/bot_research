from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from hyperliquid_ai_trader.config import Settings
from hyperliquid_ai_trader.models import AccountState, MarketSnapshot, Side, TradeDecision
from hyperliquid_ai_trader.risk import RiskEngine, RiskRejected


def _settings() -> Settings:
    return Settings.from_mapping(
        {
            "HL_test_wallet": "0x" + "1" * 40,
            "HL_test_wallet_private_key": "0x" + "2" * 64,
            "GEMINI_API_KEY": "gemini-test-key",
        }
    )


def _account(**changes: Decimal) -> AccountState:
    values = {
        "equity": Decimal("1000"),
        "withdrawable": Decimal("1000"),
        "day_start_equity": Decimal("1000"),
        "daily_realized_pnl": Decimal("0"),
        "session_peak_equity": Decimal("1000"),
    }
    values.update(changes)
    return AccountState(**values)


def _decision(side: Side = Side.LONG) -> TradeDecision:
    return TradeDecision(
        side=side,
        stop_loss_pct=Decimal("0.5"),
        take_profit_pct=Decimal("1.0"),
        confidence=Decimal("0.80"),
        thesis="literal test thesis",
        would_abstain=False,
        abstain_reason=None,
    )


def test_risk_engine_caps_long_notional_and_rounds_size_down() -> None:
    plan = RiskEngine(_settings()).create_plan(
        _decision(),
        MarketSnapshot(entry_price=Decimal("50000"), size_decimals=3),
        _account(),
    )

    assert plan.side is Side.LONG
    assert plan.size == Decimal("0.005")
    assert plan.notional == Decimal("250.000")
    assert plan.stop_loss_price == Decimal("49750.000")
    assert plan.take_profit_price == Decimal("50500.000")
    assert plan.risk_budget_usd == Decimal("10.0")


def test_risk_engine_places_short_protection_on_correct_sides() -> None:
    plan = RiskEngine(_settings()).create_plan(
        _decision(Side.SHORT),
        MarketSnapshot(entry_price=Decimal("50000"), size_decimals=3),
        _account(),
    )

    assert plan.stop_loss_price == Decimal("50250.000")
    assert plan.take_profit_price == Decimal("49500.000")


def test_risk_engine_stops_at_daily_loss_limit() -> None:
    with pytest.raises(RiskRejected, match="daily loss"):
        RiskEngine(_settings()).create_plan(
            _decision(),
            MarketSnapshot(entry_price=Decimal("50000"), size_decimals=3),
            _account(daily_realized_pnl=Decimal("-200")),
        )


def test_risk_engine_stops_at_session_drawdown_limit() -> None:
    with pytest.raises(RiskRejected, match="drawdown"):
        RiskEngine(_settings()).create_plan(
            _decision(),
            MarketSnapshot(entry_price=Decimal("50000"), size_decimals=3),
            _account(equity=Decimal("750")),
        )


def test_risk_engine_rejects_order_below_exchange_minimum() -> None:
    settings = replace(_settings(), max_position_notional_usd=Decimal("5"))

    with pytest.raises(RiskRejected, match="minimum notional"):
        RiskEngine(settings).create_plan(
            _decision(),
            MarketSnapshot(entry_price=Decimal("1000"), size_decimals=3),
            _account(),
        )


def test_risk_engine_rejects_llm_stop_outside_configured_range() -> None:
    invalid = replace(_decision(), stop_loss_pct=Decimal("1.01"))

    with pytest.raises(RiskRejected, match="stop loss"):
        RiskEngine(_settings()).create_plan(
            invalid,
            MarketSnapshot(entry_price=Decimal("50000"), size_decimals=3),
            _account(),
        )
