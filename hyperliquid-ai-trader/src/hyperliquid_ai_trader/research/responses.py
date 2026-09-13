"""Validate normalized, saved model responses without contacting a provider."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from ..agents import FunctionCall
from ..models import TradeDecision
from .config import ResearchConfig
from .data import is_research_decision_time
from .decisions import ResearchDecisionError, parse_research_trade_calls


class ResearchResponseError(ValueError):
    """Raised when saved model responses are not safe to evaluate."""


@dataclass(frozen=True)
class PreparedRequestRecord:
    decision_time_ms: int
    request_id: str
    trial_id: str
    request_hash: str
    requested_model: str
    canonical_payload: str


@dataclass(frozen=True)
class ModelResponseRecord:
    request_id: str
    returned_model: str
    received_at_ms: int
    function_calls: tuple[FunctionCall, ...]


@dataclass(frozen=True)
class ValidatedDecisionRecord:
    request: PreparedRequestRecord
    response: ModelResponseRecord
    decision: TradeDecision


def _non_empty_string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResearchResponseError(f"{name} must be a non-empty string")
    return value


def _timestamp(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchResponseError(f"{name} must be a non-negative integer")
    return value


def read_prepared_requests_jsonl(path: Path) -> list[PreparedRequestRecord]:
    """Read request artifacts and verify their canonical identity before use."""

    records: list[PreparedRequestRecord] = []
    expected_keys = {
        "decision_time_ms",
        "request_id",
        "trial_id",
        "request_hash",
        "requested_model",
        "canonical_payload",
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ResearchResponseError(f"cannot read prepared requests: {path}") from error
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) != expected_keys:
                raise TypeError("prepared request row keys are invalid")
            record = PreparedRequestRecord(
                decision_time_ms=_timestamp(row["decision_time_ms"], name="decision_time_ms"),
                request_id=_non_empty_string(row["request_id"], name="request_id"),
                trial_id=_non_empty_string(row["trial_id"], name="trial_id"),
                request_hash=_non_empty_string(row["request_hash"], name="request_hash"),
                requested_model=_non_empty_string(row["requested_model"], name="requested_model"),
                canonical_payload=_non_empty_string(row["canonical_payload"], name="canonical_payload"),
            )
            if not is_research_decision_time(record.decision_time_ms):
                raise ResearchResponseError("prepared request is off the five-minute decision grid")
            if hashlib.sha256(record.canonical_payload.encode("utf-8")).hexdigest() != record.request_hash:
                raise ResearchResponseError("prepared request hash does not match canonical payload")
            if record.request_id != f"{record.trial_id}:{record.request_hash}":
                raise ResearchResponseError("prepared request ID does not match trial and hash")
            payload = json.loads(record.canonical_payload)
            if not isinstance(payload, dict):
                raise TypeError("canonical payload must be an object")
            if payload.get("requested_model") != record.requested_model:
                raise ResearchResponseError("prepared request model does not match canonical payload")
            input_data = payload.get("input_data")
            if not isinstance(input_data, dict):
                raise TypeError("canonical request input_data must be an object")
            market = input_data.get("market")
            if not isinstance(market, dict) or market.get("as_of_ms", record.decision_time_ms) > record.decision_time_ms:
                raise ResearchResponseError("prepared request uses future market features")
            records.append(record)
        except (TypeError, ValueError, json.JSONDecodeError, ResearchResponseError) as error:
            raise ResearchResponseError(f"invalid prepared request at line {line_number}") from error
    if not records:
        raise ResearchResponseError("prepared request artifact is empty")
    request_ids = [record.request_id for record in records]
    if len(set(request_ids)) != len(request_ids):
        raise ResearchResponseError("prepared request IDs must be unique")
    return records


def read_model_responses_jsonl(path: Path) -> list[ModelResponseRecord]:
    """Read provider-neutral response rows; raw provider payloads stay separate."""

    records: list[ModelResponseRecord] = []
    expected_keys = {"request_id", "returned_model", "received_at_ms", "function_calls"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ResearchResponseError(f"cannot read model responses: {path}") from error
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) != expected_keys:
                raise TypeError("model response row keys are invalid")
            raw_calls = row["function_calls"]
            if not isinstance(raw_calls, list):
                raise TypeError("function_calls must be an array")
            calls: list[FunctionCall] = []
            for raw_call in raw_calls:
                if not isinstance(raw_call, dict) or set(raw_call) != {"name", "args"}:
                    raise TypeError("function call keys are invalid")
                if not isinstance(raw_call["name"], str) or not isinstance(raw_call["args"], dict):
                    raise TypeError("function call values are invalid")
                calls.append(FunctionCall(name=raw_call["name"], args=dict(raw_call["args"])))
            records.append(
                ModelResponseRecord(
                    request_id=_non_empty_string(row["request_id"], name="request_id"),
                    returned_model=_non_empty_string(row["returned_model"], name="returned_model"),
                    received_at_ms=_timestamp(row["received_at_ms"], name="received_at_ms"),
                    function_calls=tuple(calls),
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError, ResearchResponseError) as error:
            raise ResearchResponseError(f"invalid model response at line {line_number}") from error
    if not records:
        raise ResearchResponseError("model response artifact is empty")
    request_ids = [record.request_id for record in records]
    if len(set(request_ids)) != len(request_ids):
        raise ResearchResponseError("model response request IDs must be unique")
    return records


def validate_model_responses(
    *,
    config: ResearchConfig,
    requests: list[PreparedRequestRecord],
    responses: list[ModelResponseRecord],
) -> tuple[ValidatedDecisionRecord, ...]:
    """Match exactly one bounded response to every prepared request."""

    request_ids = {request.request_id for request in requests}
    responses_by_id = {response.request_id: response for response in responses}
    if set(responses_by_id) != request_ids:
        raise ResearchResponseError("model responses must match prepared request IDs exactly")
    records: list[ValidatedDecisionRecord] = []
    for request in requests:
        response = responses_by_id[request.request_id]
        if request.requested_model != config.trader_model:
            raise ResearchResponseError("prepared request model does not match current research config")
        if response.returned_model != request.requested_model:
            raise ResearchResponseError("returned model does not match requested model")
        if response.received_at_ms < request.decision_time_ms:
            raise ResearchResponseError("model response precedes its decision time")
        try:
            decision = parse_research_trade_calls(
                list(response.function_calls),
                limits=config.decision_limits,
            )
        except ResearchDecisionError as error:
            raise ResearchResponseError("model response has an invalid research decision") from error
        records.append(ValidatedDecisionRecord(request=request, response=response, decision=decision))
    return tuple(records)


def write_validated_decisions_jsonl(path: Path, records: tuple[ValidatedDecisionRecord, ...]) -> None:
    """Atomically persist bounded decisions for a later, separate simulator step."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            decision = record.decision
            handle.write(
                json.dumps(
                    {
                        "decision_time_ms": record.request.decision_time_ms,
                        "request_id": record.request.request_id,
                        "trial_id": record.request.trial_id,
                        "request_hash": record.request.request_hash,
                        "requested_model": record.request.requested_model,
                        "returned_model": record.response.returned_model,
                        "received_at_ms": record.response.received_at_ms,
                        "decision": {
                            "side": decision.side.value,
                            "stop_loss_pct": format(decision.stop_loss_pct, "f"),
                            "take_profit_pct": format(decision.take_profit_pct, "f"),
                            "confidence": format(decision.confidence, "f"),
                            "thesis": decision.thesis,
                            "would_abstain": decision.would_abstain,
                            "abstain_reason": decision.abstain_reason,
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
            handle.write("\n")
    temporary.replace(path)
