"""Canonical, offline identities for future model and Batch requests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from math import isfinite
from typing import Any, Mapping


class ModelRequestError(ValueError):
    """Raised when a model request cannot be canonically recorded."""


@dataclass(frozen=True)
class PreparedModelRequest:
    """The immutable input identity saved before any model API submission."""

    request_id: str
    trial_id: str
    request_hash: str
    requested_model: str
    canonical_payload: str


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ModelRequestError("floating request values must be finite")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ModelRequestError("decimal request values must be finite")
        return format(value, "f")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ModelRequestError("request object keys must be strings")
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    raise ModelRequestError(f"unsupported request value: {type(value).__name__}")


def build_model_request(
    *,
    trial_id: str,
    input_data: Mapping[str, Any],
    constitution: str,
    instruction: str,
    strategy: Mapping[str, Any],
    requested_model: str,
    temperature: int | float | Decimal,
    thinking: str,
    max_output_tokens: int,
    tool_schema: Mapping[str, Any],
    feature_set: str,
) -> PreparedModelRequest:
    """Hash every field that can change a model response, excluding trial ID.

    A later independent retry receives a new ``trial_id`` but preserves the
    same ``request_hash`` when the actual model input is identical.
    """

    if not all(isinstance(value, str) and value for value in (trial_id, constitution, instruction, requested_model, thinking, feature_set)):
        raise ModelRequestError("request identifiers, prompts, and model fields must be non-empty strings")
    if not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
        raise ModelRequestError("max_output_tokens must be a positive integer")
    payload = _json_value(
        {
            "input_data": input_data,
            "constitution": constitution,
            "instruction": instruction,
            "strategy": strategy,
            "requested_model": requested_model,
            "temperature": temperature,
            "thinking": thinking,
            "max_output_tokens": max_output_tokens,
            "tool_schema": tool_schema,
            "feature_set": feature_set,
        }
    )
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    request_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return PreparedModelRequest(
        request_id=f"{trial_id}:{request_hash}",
        trial_id=trial_id,
        request_hash=request_hash,
        requested_model=requested_model,
        canonical_payload=canonical_payload,
    )
