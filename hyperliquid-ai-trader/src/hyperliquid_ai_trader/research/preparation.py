"""Prepare immutable, non-submitting model inputs for offline research."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..agents import OPEN_POSITION_JSON_SCHEMA
from .config import ResearchConfig
from .points import PointCandidate
from .request_identity import PreparedModelRequest, build_model_request


class ResearchPreparationError(ValueError):
    """Raised when a public research input cannot be prepared safely."""


_TRIAL_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_FORBIDDEN_SECRET_KEYS = {"api_key", "private_key", "wallet", "secret"}


@dataclass(frozen=True)
class PreparedPointRequest:
    decision_time_ms: int
    request: PreparedModelRequest


def _format_decimal(value: Decimal) -> str:
    return format(value, "f")


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_SECRET_KEYS:
                raise ResearchPreparationError(f"strategy must not contain {key}")
            _reject_secret_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret_keys(child)


def read_text_asset(path: Path, *, name: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ResearchPreparationError(f"cannot read {name}: {path}") from error
    if not value:
        raise ResearchPreparationError(f"{name} must not be empty")
    return value


def read_strategy_asset(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResearchPreparationError(f"cannot read strategy: {path}") from error
    if not isinstance(value, dict):
        raise ResearchPreparationError("strategy must be a JSON object")
    _reject_secret_keys(value)
    return value


def _input_data(config: ResearchConfig, point: PointCandidate) -> dict[str, Any]:
    return {
        "market": {
            "venue": point.venue,
            "symbol": point.symbol,
            **point.features.to_prompt_dict(),
        },
        "execution": {
            "model_delay_ms": config.execution.model_delay_ms,
            "max_arrival_delay_ms": config.execution.max_arrival_delay_ms,
            "max_hold_ms": config.execution.max_hold_ms,
            "fee_rate": _format_decimal(config.execution.fee_rate),
            "spread_bps": _format_decimal(config.execution.spread_bps),
            "slippage_bps": _format_decimal(config.execution.slippage_bps),
        },
        "decision_limits": {
            "min_stop_loss_pct": _format_decimal(config.decision_limits.min_stop_loss_pct),
            "max_stop_loss_pct": _format_decimal(config.decision_limits.max_stop_loss_pct),
            "min_take_profit_pct": _format_decimal(config.decision_limits.min_take_profit_pct),
            "max_take_profit_pct": _format_decimal(config.decision_limits.max_take_profit_pct),
        },
    }


def prepare_point_requests(
    *,
    config: ResearchConfig,
    points: list[PointCandidate],
    constitution: str,
    instruction: str,
    strategy: Mapping[str, Any],
    trial_prefix: str,
    temperature: Decimal,
    thinking: str,
    max_output_tokens: int,
) -> tuple[PreparedPointRequest, ...]:
    """Create deterministic request identities without contacting a model API."""

    if not _TRIAL_PREFIX.fullmatch(trial_prefix):
        raise ResearchPreparationError("trial_prefix must use letters, digits, dots, underscores, or hyphens")
    if not temperature.is_finite():
        raise ResearchPreparationError("temperature must be finite")
    if not points:
        raise ResearchPreparationError("at least one selected point is required")
    requests: list[PreparedPointRequest] = []
    for point in points:
        if point.venue != config.market_venue or point.symbol != config.symbol:
            raise ResearchPreparationError("point market does not match the research config")
        trial_id = f"{trial_prefix}-{point.decision_time_ms}"
        request = build_model_request(
            trial_id=trial_id,
            input_data=_input_data(config, point),
            constitution=constitution,
            instruction=instruction,
            strategy=strategy,
            requested_model=config.trader_model,
            temperature=temperature,
            thinking=thinking,
            max_output_tokens=max_output_tokens,
            tool_schema={"name": "open_position", "parameters": OPEN_POSITION_JSON_SCHEMA},
            feature_set=config.feature_set,
        )
        requests.append(PreparedPointRequest(decision_time_ms=point.decision_time_ms, request=request))
    request_ids = [prepared.request.request_id for prepared in requests]
    if len(set(request_ids)) != len(request_ids):
        raise ResearchPreparationError("prepared request IDs must be unique")
    return tuple(requests)


def write_prepared_requests_jsonl(path: Path, requests: tuple[PreparedPointRequest, ...]) -> None:
    """Atomically persist prepared requests; this is not a Batch submission."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for prepared in requests:
            request = prepared.request
            handle.write(
                json.dumps(
                    {
                        "decision_time_ms": prepared.decision_time_ms,
                        "request_id": request.request_id,
                        "trial_id": request.trial_id,
                        "request_hash": request.request_hash,
                        "requested_model": request.requested_model,
                        "canonical_payload": request.canonical_payload,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
            handle.write("\n")
    temporary.replace(path)
