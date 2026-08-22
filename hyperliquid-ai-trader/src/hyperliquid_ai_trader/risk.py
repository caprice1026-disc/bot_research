"""Hard risk limits that cannot be changed by the model."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from .config import Settings
from .models import AccountState, MarketSnapshot, OrderPlan, Side, TradeDecision


class RiskRejected(ValueError):
    """Raised when a proposed trade violates a hard safety constraint."""


class RiskEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create_plan(
        self,
        decision: TradeDecision,
        market: MarketSnapshot,
        account: AccountState,
    ) -> OrderPlan:
        settings = self._settings
        if not settings.min_stop_loss_pct <= decision.stop_loss_pct <= settings.max_stop_loss_pct:
            raise RiskRejected("stop loss is outside the configured range")
        if not settings.min_take_profit_pct <= decision.take_profit_pct <= settings.max_take_profit_pct:
            raise RiskRejected("take profit is outside the configured range")
        if not Decimal("0") <= decision.confidence <= Decimal("1"):
            raise RiskRejected("confidence must be between zero and one")
        if market.entry_price <= 0:
            raise RiskRejected("entry price must be positive")

        daily_limit = account.day_start_equity * settings.max_daily_loss_pct / Decimal("100")
        if -account.daily_realized_pnl >= daily_limit:
            raise RiskRejected("daily loss limit reached")

        if account.session_peak_equity > 0:
            drawdown_pct = (
                (account.session_peak_equity - account.equity)
                / account.session_peak_equity
                * Decimal("100")
            )
            if drawdown_pct >= settings.max_drawdown_pct:
                raise RiskRejected("session drawdown limit reached")

        risk_budget = account.equity * settings.risk_per_trade_pct / Decimal("100")
        stop_fraction = decision.stop_loss_pct / Decimal("100")
        risk_notional = risk_budget / stop_fraction
        margin_capacity = account.withdrawable * Decimal(settings.leverage)
        target_notional = min(
            risk_notional,
            settings.max_position_notional_usd,
            margin_capacity,
        )

        size_quantum = Decimal(1).scaleb(-market.size_decimals)
        size = (target_notional / market.entry_price).quantize(size_quantum, rounding=ROUND_DOWN)
        notional = size * market.entry_price
        if size <= 0 or notional < Decimal("10"):
            raise RiskRejected("order is below the exchange minimum notional")

        stop_fraction = decision.stop_loss_pct / Decimal("100")
        take_fraction = decision.take_profit_pct / Decimal("100")
        if decision.side is Side.LONG:
            stop_price = market.entry_price * (Decimal("1") - stop_fraction)
            take_price = market.entry_price * (Decimal("1") + take_fraction)
        else:
            stop_price = market.entry_price * (Decimal("1") + stop_fraction)
            take_price = market.entry_price * (Decimal("1") - take_fraction)

        price_quantum = Decimal("0.001")
        return OrderPlan(
            side=decision.side,
            size=size,
            notional=notional,
            entry_price=market.entry_price,
            stop_loss_price=stop_price.quantize(price_quantum),
            take_profit_price=take_price.quantize(price_quantum),
            risk_budget_usd=risk_budget,
            decision=decision,
        )
