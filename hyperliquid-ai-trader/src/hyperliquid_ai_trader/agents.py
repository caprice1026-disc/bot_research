"""Validated agent boundary for Gemini Function Calling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import time
from typing import Any, Callable, Protocol

from .models import Side, TradeDecision
from .strategy import StrategyPatchError, apply_strategy_patch


@dataclass
class FunctionCall:
    name: str
    args: dict[str, Any]


class ModelGatewayError(RuntimeError):
    def __init__(self, error_type: str, *, retryable: bool) -> None:
        super().__init__(error_type)
        self.error_type = error_type
        self.retryable = retryable


class AgentDecisionError(RuntimeError):
    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


class TraderGateway(Protocol):
    def generate_trade(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> list[FunctionCall]: ...


class ReviewGateway(Protocol):
    def generate_review(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DecisionEnvelope:
    decision: TradeDecision
    prompt_hash: str
    model: str


@dataclass(frozen=True)
class ReviewEnvelope:
    state: dict[str, Any]
    patch: dict[str, Any]
    prompt_hash: str
    model: str


_EXPECTED_ARGS = {
    "side",
    "stop_loss_pct",
    "take_profit_pct",
    "confidence",
    "thesis",
    "would_abstain",
    "abstain_reason",
}


def _parse_call(calls: list[FunctionCall]) -> TradeDecision:
    if len(calls) != 1 or calls[0].name != "open_position":
        raise AgentDecisionError("invalid_function_call")
    args = calls[0].args
    if set(args) != _EXPECTED_ARGS:
        raise AgentDecisionError("invalid_function_call")
    try:
        side = Side(str(args["side"]).lower())
        stop_loss_pct = Decimal(str(args["stop_loss_pct"]))
        take_profit_pct = Decimal(str(args["take_profit_pct"]))
        confidence = Decimal(str(args["confidence"]))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise AgentDecisionError("invalid_function_call") from exc
    thesis = args["thesis"]
    would_abstain = args["would_abstain"]
    abstain_reason = args["abstain_reason"]
    if not isinstance(thesis, str) or not thesis.strip() or len(thesis) > 1_000:
        raise AgentDecisionError("invalid_function_call")
    if not isinstance(would_abstain, bool):
        raise AgentDecisionError("invalid_function_call")
    if abstain_reason is not None and not isinstance(abstain_reason, str):
        raise AgentDecisionError("invalid_function_call")
    if would_abstain and not abstain_reason:
        raise AgentDecisionError("invalid_function_call")
    if not Decimal("0") <= confidence <= Decimal("1"):
        raise AgentDecisionError("invalid_function_call")
    return TradeDecision(
        side=side,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        confidence=confidence,
        thesis=thesis.strip(),
        would_abstain=would_abstain,
        abstain_reason=abstain_reason.strip() if isinstance(abstain_reason, str) else None,
    )


class TraderAgent:
    def __init__(
        self,
        *,
        gateway: TraderGateway,
        model: str,
        temperature: float,
        constitution: str,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.temperature = temperature
        self.constitution = constitution.strip()
        self.max_attempts = max_attempts
        self.sleeper = sleeper

    def decide(self, context: dict[str, Any]) -> DecisionEnvelope:
        prompt = self.constitution + "\n\nCURRENT_CONTEXT\n" + json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        last_error = "model_error"
        for attempt in range(self.max_attempts):
            try:
                calls = self.gateway.generate_trade(
                    prompt=prompt,
                    model=self.model,
                    temperature=self.temperature,
                )
                decision = _parse_call(calls)
                return DecisionEnvelope(decision, prompt_hash, self.model)
            except ModelGatewayError as exc:
                last_error = exc.error_type
                if not exc.retryable:
                    break
            except AgentDecisionError as exc:
                last_error = exc.error_type
            if attempt + 1 < self.max_attempts:
                self.sleeper(float(2**attempt))
        raise AgentDecisionError(last_error)


class ReviewerAgent:
    def __init__(
        self,
        *,
        gateway: ReviewGateway,
        model: str,
        temperature: float,
        constitution: str,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.temperature = temperature
        self.constitution = constitution.strip()
        self.max_attempts = max_attempts
        self.sleeper = sleeper

    def review(
        self,
        *,
        strategy: dict[str, Any],
        closed_trades: list[dict[str, Any]],
        review_cycle: int,
    ) -> ReviewEnvelope:
        prompt = self.constitution + "\n\nREVIEW_CONTEXT\n" + json.dumps(
            {"strategy": strategy, "closed_trades": closed_trades, "review_cycle": review_cycle},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        last_error = "review_error"
        for attempt in range(self.max_attempts):
            try:
                patch = self.gateway.generate_review(
                    prompt=prompt,
                    model=self.model,
                    temperature=self.temperature,
                )
                state = apply_strategy_patch(strategy, patch, review_cycle=review_cycle)
                return ReviewEnvelope(state, patch, prompt_hash, self.model)
            except ModelGatewayError as exc:
                last_error = exc.error_type
                if not exc.retryable:
                    break
            except StrategyPatchError:
                last_error = "invalid_strategy_patch"
            if attempt + 1 < self.max_attempts:
                self.sleeper(float(2**attempt))
        raise AgentDecisionError(last_error)
