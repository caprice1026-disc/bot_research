"""Research-only validation for non-executing model trade proposals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..agents import AgentDecisionError, FunctionCall, parse_trade_calls
from ..models import TradeDecision


class ResearchDecisionError(ValueError):
    """Raised when a proposal cannot be used by the research simulator."""


@dataclass(frozen=True)
class ResearchDecisionLimits:
    min_stop_loss_pct: Decimal
    max_stop_loss_pct: Decimal
    min_take_profit_pct: Decimal
    max_take_profit_pct: Decimal

    def __post_init__(self) -> None:
        values = (
            self.min_stop_loss_pct,
            self.max_stop_loss_pct,
            self.min_take_profit_pct,
            self.max_take_profit_pct,
        )
        if not all(value.is_finite() for value in values):
            raise ResearchDecisionError("decision limits must be finite")
        if self.min_stop_loss_pct <= 0 or self.max_stop_loss_pct < self.min_stop_loss_pct:
            raise ResearchDecisionError("stop loss limits are invalid")
        if self.min_take_profit_pct <= 0 or self.max_take_profit_pct < self.min_take_profit_pct:
            raise ResearchDecisionError("take profit limits are invalid")


def validate_research_decision(
    decision: TradeDecision,
    *,
    limits: ResearchDecisionLimits,
) -> TradeDecision:
    values = (decision.stop_loss_pct, decision.take_profit_pct, decision.confidence)
    if not all(value.is_finite() for value in values):
        raise ResearchDecisionError("proposal values must be finite")
    if not limits.min_stop_loss_pct <= decision.stop_loss_pct <= limits.max_stop_loss_pct:
        raise ResearchDecisionError("stop loss is outside the configured range")
    if not limits.min_take_profit_pct <= decision.take_profit_pct <= limits.max_take_profit_pct:
        raise ResearchDecisionError("take profit is outside the configured range")
    return decision


def parse_research_trade_calls(
    calls: list[FunctionCall],
    *,
    limits: ResearchDecisionLimits,
) -> TradeDecision:
    """Reuse the live schema parser, then apply research SL/TP limits."""

    try:
        decision = parse_trade_calls(calls)
    except AgentDecisionError as error:
        raise ResearchDecisionError("invalid_function_call") from error
    return validate_research_decision(decision, limits=limits)
