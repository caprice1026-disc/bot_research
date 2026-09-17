"""Strict parsing for the v1 target-position model response."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class DecisionError(ValueError):
    """Raised when a model response cannot safely become a target decision."""


_KEYS = frozenset({"schema_version", "decision_id", "intent", "target_fraction", "stop_price", "thesis", "invalidation"})
_FRACTIONS = frozenset(
    {Decimal("-1"), Decimal("-0.5"), Decimal("-0.25"), Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("1")}
)


@dataclass(frozen=True)
class DecisionContext:
    decision_id: str
    position_version: int


@dataclass(frozen=True)
class TargetDecision:
    decision_id: str
    intent: str
    target_fraction: Decimal | None
    stop_price: Decimal | None
    thesis: str
    invalidation: str
    source_position_version: int


def _decimal_string(value: Any, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise DecisionError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise DecisionError(f"{field} is not a decimal") from error
    if not parsed.is_finite():
        raise DecisionError(f"{field} must be finite")
    return parsed


def parse_target_decision(payload: dict[str, Any], context: DecisionContext) -> TargetDecision:
    """Reject unknown keys, wrong IDs, and values the fixed Risk layer cannot own."""

    if set(payload) != _KEYS:
        raise DecisionError("response keys do not match target_position schema v1")
    if payload.get("schema_version") != 1 or payload.get("decision_id") != context.decision_id:
        raise DecisionError("response schema version or decision ID does not match the request")
    intent = payload.get("intent")
    if intent not in {"hold", "set_target"}:
        raise DecisionError("intent must be hold or set_target")
    thesis = payload.get("thesis")
    invalidation = payload.get("invalidation")
    if not all(isinstance(value, str) and 0 < len(value) <= 500 for value in (thesis, invalidation)):
        raise DecisionError("thesis and invalidation must be non-empty strings up to 500 characters")
    fraction_value = payload.get("target_fraction")
    stop_value = payload.get("stop_price")
    if intent == "hold":
        if fraction_value is not None or stop_value is not None:
            raise DecisionError("hold requires null target_fraction and stop_price")
        return TargetDecision(context.decision_id, intent, None, None, thesis, invalidation, context.position_version)
    fraction = _decimal_string(fraction_value, field="target_fraction")
    if fraction not in _FRACTIONS:
        raise DecisionError("target_fraction is not an allowed discrete value")
    if fraction == 0:
        if stop_value is not None:
            raise DecisionError("flat targets require a null stop_price")
        stop = None
    else:
        stop = _decimal_string(stop_value, field="stop_price")
        if stop <= 0:
            raise DecisionError("stop_price must be positive")
    return TargetDecision(context.decision_id, intent, fraction, stop, thesis, invalidation, context.position_version)
