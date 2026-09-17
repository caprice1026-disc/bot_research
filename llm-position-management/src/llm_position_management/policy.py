"""Policy boundary used by offline fixtures before a live model adapter is added."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class PolicyResponse:
    """A saved model response, including the cost reserved for this call."""

    payload: dict[str, Any] | None
    received_at_ms: int
    estimated_cost_usd: Decimal


class PositionPolicy(Protocol):
    def decide(self, observation: dict[str, Any], request_id: str) -> PolicyResponse: ...


class ScriptedPolicy:
    """Deterministic fixture policy keyed by the persisted decision ID."""

    def __init__(self, responses: Mapping[str, dict[str, Any] | PolicyResponse | None]) -> None:
        self._responses = dict(responses)

    def decide(self, observation: dict[str, Any], request_id: str) -> PolicyResponse:
        response = self._responses.get(request_id)
        if isinstance(response, PolicyResponse):
            return response
        return PolicyResponse(
            payload=response,
            received_at_ms=int(observation["as_of_ms"]),
            estimated_cost_usd=Decimal("0"),
        )
