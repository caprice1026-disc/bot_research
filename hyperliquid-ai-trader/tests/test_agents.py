from __future__ import annotations

from decimal import Decimal

import pytest

from hyperliquid_ai_trader.agents import (
    AgentDecisionError,
    FunctionCall,
    ModelGatewayError,
    ReviewerAgent,
    TraderAgent,
)
from hyperliquid_ai_trader.strategy import initial_strategy
from hyperliquid_ai_trader.models import Side


class SequenceGateway:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.prompts: list[str] = []

    def generate_trade(self, *, prompt: str, model: str, temperature: float) -> list[FunctionCall]:
        self.prompts.append(prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


class ReviewGateway:
    def __init__(self, patch: dict) -> None:
        self.patch = patch

    def generate_review(self, *, prompt: str, model: str, temperature: float) -> dict:
        return self.patch


def _valid_call() -> FunctionCall:
    return FunctionCall(
        name="open_position",
        args={
            "side": "long",
            "stop_loss_pct": 0.5,
            "take_profit_pct": 1.0,
            "confidence": 0.72,
            "thesis": "momentum and book agree",
            "would_abstain": False,
            "abstain_reason": None,
        },
    )


def test_trader_accepts_exactly_one_valid_open_position_call() -> None:
    agent = TraderAgent(
        gateway=SequenceGateway([[_valid_call()]]),
        model="gemini-test",
        temperature=0.7,
        constitution="fixed constitution",
        max_attempts=3,
        sleeper=lambda _: None,
    )

    envelope = agent.decide({"market": {"mid": 50000}, "strategy": {"version": 1}})

    assert envelope.decision.side is Side.LONG
    assert envelope.decision.stop_loss_pct == Decimal("0.5")
    assert envelope.decision.confidence == Decimal("0.72")
    assert envelope.model == "gemini-test"
    assert len(envelope.prompt_hash) == 64


def test_trader_rejects_multiple_function_calls_without_executing_any() -> None:
    gateway = SequenceGateway([[ _valid_call(), _valid_call() ]] * 3)
    agent = TraderAgent(
        gateway=gateway,
        model="gemini-test",
        temperature=0.7,
        constitution="fixed",
        max_attempts=3,
        sleeper=lambda _: None,
    )

    with pytest.raises(AgentDecisionError) as caught:
        agent.decide({"market": {"mid": 50000}})

    assert caught.value.error_type == "invalid_function_call"
    assert len(gateway.prompts) == 3


def test_trader_stops_after_rate_limit_without_retrying() -> None:
    sleeps: list[float] = []
    gateway = SequenceGateway(
        [ModelGatewayError("rate_limited", retryable=True), [_valid_call()]]
    )
    agent = TraderAgent(
        gateway=gateway,
        model="gemini-test",
        temperature=0.7,
        constitution="fixed",
        max_attempts=3,
        sleeper=sleeps.append,
    )

    with pytest.raises(AgentDecisionError, match="rate_limited"):
        agent.decide({"market": {"mid": 50000}})

    assert len(gateway.prompts) == 1
    assert sleeps == []


def test_trader_does_not_burn_retries_on_rate_limit() -> None:
    sleeps: list[float] = []
    gateway = SequenceGateway(
        [ModelGatewayError("rate_limited", retryable=True), [_valid_call()]]
    )
    agent = TraderAgent(
        gateway=gateway,
        model="gemini-test",
        temperature=0.7,
        constitution="fixed",
        max_attempts=3,
        sleeper=sleeps.append,
    )

    with pytest.raises(AgentDecisionError, match="rate_limited"):
        agent.decide({"market": {"mid": 50000}})

    assert len(gateway.prompts) == 1
    assert sleeps == []


def test_trader_rejects_unknown_function_and_out_of_range_confidence() -> None:
    unknown = FunctionCall(name="withdraw", args={})
    bad_confidence = _valid_call()
    bad_confidence.args["confidence"] = 1.5
    gateway = SequenceGateway([[unknown], [bad_confidence], [bad_confidence]])
    agent = TraderAgent(
        gateway=gateway,
        model="gemini-test",
        temperature=0.7,
        constitution="fixed",
        max_attempts=3,
        sleeper=lambda _: None,
    )

    with pytest.raises(AgentDecisionError) as caught:
        agent.decide({"market": {"mid": 50000}})

    assert caught.value.error_type == "invalid_function_call"


def test_reviewer_applies_allowlisted_patch_to_new_version() -> None:
    patch = {
        "base_version": 1,
        "summary": "short trades improved",
        "operations": [
            {
                "op": "add",
                "path": "/active_rules",
                "value": "negative 5m return supports SHORT",
                "evidence": {"trade_ids": [4, 5], "net_pnl": 2.1},
            }
        ],
    }
    reviewer = ReviewerAgent(
        gateway=ReviewGateway(patch),
        model="gemini-review",
        temperature=0.4,
        constitution="review only strategy evidence",
    )

    result = reviewer.review(
        strategy=initial_strategy(),
        closed_trades=[{"trade_id": 4, "net_pnl": 1.0}, {"trade_id": 5, "net_pnl": 1.1}],
        review_cycle=6,
    )

    assert result.state["version"] == 2
    assert result.patch == patch
    assert len(result.prompt_hash) == 64
