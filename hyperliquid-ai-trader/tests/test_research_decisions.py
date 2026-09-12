from __future__ import annotations

from decimal import Decimal

import pytest

from hyperliquid_ai_trader.agents import FunctionCall
from hyperliquid_ai_trader.models import Side
from hyperliquid_ai_trader.research.decisions import (
    ResearchDecisionError,
    ResearchDecisionLimits,
    parse_research_trade_calls,
)


def _limits() -> ResearchDecisionLimits:
    return ResearchDecisionLimits(
        min_stop_loss_pct=Decimal("0.10"),
        max_stop_loss_pct=Decimal("1.00"),
        min_take_profit_pct=Decimal("0.10"),
        max_take_profit_pct=Decimal("2.00"),
    )


def _call(*, stop_loss_pct: str = "0.30", would_abstain: bool = False) -> FunctionCall:
    return FunctionCall(
        name="open_position",
        args={
            "side": "long",
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": "0.60",
            "confidence": "0.60",
            "thesis": "fixture",
            "would_abstain": would_abstain,
            "abstain_reason": "no clear edge" if would_abstain else None,
        },
    )


def test_research_parser_accepts_one_bounded_trade_proposal() -> None:
    decision = parse_research_trade_calls([_call()], limits=_limits())

    assert decision.side is Side.LONG
    assert decision.stop_loss_pct == Decimal("0.30")


def test_research_parser_rejects_out_of_range_abstention_reference() -> None:
    with pytest.raises(ResearchDecisionError, match="stop loss"):
        parse_research_trade_calls([_call(stop_loss_pct="1.01", would_abstain=True)], limits=_limits())
