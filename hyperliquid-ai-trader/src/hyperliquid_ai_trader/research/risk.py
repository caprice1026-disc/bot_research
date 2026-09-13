"""Risk limits for the offline sequential research replay."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .simulator import VirtualAccount


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    quantity: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")


@dataclass(frozen=True)
class ResearchRiskEngine:
    risk_per_trade_pct: Decimal
    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal
    max_position_notional_usd: Decimal
    leverage: Decimal
    min_notional_usd: Decimal

    def __post_init__(self) -> None:
        values = (self.risk_per_trade_pct, self.max_daily_loss_pct, self.max_drawdown_pct, self.max_position_notional_usd, self.leverage, self.min_notional_usd)
        if not all(value.is_finite() and value >= 0 for value in values) or self.leverage <= 0:
            raise ValueError("risk settings must be finite and non-negative")

    def check_entry(self, *, account: VirtualAccount, entry_price: Decimal, stop_loss_pct: Decimal, position_open: bool) -> RiskDecision:
        if position_open:
            return RiskDecision(False, "position_open")
        if account.daily_realized_pnl <= -(account.day_start_equity * self.max_daily_loss_pct / Decimal("100")):
            return RiskDecision(False, "daily_loss_limit")
        if account.drawdown_pct >= self.max_drawdown_pct:
            return RiskDecision(False, "max_drawdown")
        if entry_price <= 0 or stop_loss_pct <= 0:
            return RiskDecision(False, "invalid_risk_input")
        risk_budget = account.equity * self.risk_per_trade_pct / Decimal("100")
        notional = min(risk_budget / (stop_loss_pct / Decimal("100")), self.max_position_notional_usd, account.equity * self.leverage)
        if notional < self.min_notional_usd:
            return RiskDecision(False, "min_notional")
        return RiskDecision(True, "allowed", quantity=notional / entry_price, notional=notional)
