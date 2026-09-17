"""Gemini Batch adapter for immutable, offline research requests."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from google import genai
from google.genai import types

from ..agents import OPEN_POSITION_JSON_SCHEMA
from .batch import BatchError
from .request_identity import PreparedModelRequest


_TERMINAL_FAILURE_STATES = {
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}
_PENDING_STATES = {
    "JOB_STATE_QUEUED",
    "JOB_STATE_PENDING",
    "JOB_STATE_RUNNING",
    "JOB_STATE_PAUSED",
    "JOB_STATE_UPDATING",
}


def _gemini_open_position_schema() -> dict[str, Any]:
    """Return the Function Calling subset accepted by Gemini GenerateContent.

    The shared schema is deliberately strict for local validation, but Gemini
    function declarations reject ``additionalProperties`` and do not need a
    nullable abstention field.  The local parser keeps the strict boundary;
    this adapter only expresses the equivalent wire contract.
    """

    schema = deepcopy(OPEN_POSITION_JSON_SCHEMA)
    schema.pop("additionalProperties", None)
    schema["properties"]["abstain_reason"] = {
        "type": "string",
        "description": "Reason for abstaining; use an empty string when would_abstain is false.",
    }
    return schema


def _state_name(job: Any) -> str:
    state = getattr(job, "state", None)
    return str(getattr(state, "value", state or ""))


def _request_prompt(payload: dict[str, Any]) -> tuple[str, str]:
    try:
        constitution = payload["constitution"]
        instruction = payload["instruction"]
        strategy = payload["strategy"]
        observation = payload["input_data"]
    except KeyError as error:
        raise BatchError("prepared Gemini request is incomplete") from error
    if not all(isinstance(value, str) and value for value in (constitution, instruction)):
        raise BatchError("prepared Gemini prompt is invalid")
    if not isinstance(strategy, dict) or not isinstance(observation, dict):
        raise BatchError("prepared Gemini strategy or observation is invalid")
    system_instruction = (
        constitution
        + "\n\nFIXED_STRATEGY\n"
        + json.dumps(strategy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n\n"
        + instruction
    )
    contents = "CURRENT_OBSERVATION\n" + json.dumps(
        observation, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return system_instruction, contents


class GeminiBatchProvider:
    """Translate prepared research requests to Gemini's inline Batch format."""

    def __init__(self, *, model: str, api_key: str | None = None, client: Any | None = None) -> None:
        if not model:
            raise BatchError("Gemini model is required")
        if client is None:
            if not api_key:
                raise BatchError("Gemini API key is required")
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=30_000,
                    client_args={"trust_env": False},
                    async_client_args={"trust_env": False},
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        self.client = client
        self.model = model

    def submit(self, requests: list[PreparedModelRequest]) -> str:
        if not requests:
            raise BatchError("Gemini Batch requires at least one request")
        inline_requests: list[types.InlinedRequest] = []
        for request in requests:
            payload = json.loads(request.canonical_payload)
            if payload.get("requested_model") != self.model or request.requested_model != self.model:
                raise BatchError("prepared request model does not match Gemini Batch model")
            if payload.get("thinking") != "minimal":
                raise BatchError("Gemini 3.5 Flash Batch pilot requires thinking=minimal")
            max_output_tokens = payload.get("max_output_tokens")
            temperature = payload.get("temperature")
            if not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
                raise BatchError("prepared Gemini max_output_tokens is invalid")
            try:
                parsed_temperature = Decimal(str(temperature))
            except (InvalidOperation, ValueError):
                parsed_temperature = Decimal("NaN")
            if isinstance(temperature, bool) or not parsed_temperature.is_finite():
                raise BatchError("prepared Gemini temperature is invalid")
            system_instruction, contents = _request_prompt(payload)
            declaration = types.FunctionDeclaration(
                name="open_position",
                description="Return one bounded research trade proposal.",
                parameters_json_schema=_gemini_open_position_schema(),
            )
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=float(parsed_temperature),
                max_output_tokens=max_output_tokens,
                tools=[types.Tool(function_declarations=[declaration])],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.ANY,
                        allowed_function_names=["open_position"],
                    )
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            )
            inline_requests.append(
                types.InlinedRequest(
                    contents=contents,
                    config=config,
                    metadata={"request_id": request.request_id},
                )
            )
        job = self.client.batches.create(model=self.model, src=inline_requests)
        name = getattr(job, "name", None)
        if not isinstance(name, str) or not name:
            raise BatchError("Gemini Batch did not return a job name")
        return name

    def sync(self, provider_job_id: str) -> list[dict[str, Any]]:
        job = self.client.batches.get(name=provider_job_id)
        state = _state_name(job)
        if state in _PENDING_STATES:
            return []
        if state in _TERMINAL_FAILURE_STATES:
            raise BatchError(f"Gemini Batch ended in {state}")
        if state not in {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"}:
            raise BatchError(f"Gemini Batch returned an unknown state: {state or 'missing'}")
        destination = getattr(job, "dest", None)
        rows = getattr(destination, "inlined_responses", None)
        if not isinstance(rows, list):
            raise BatchError("Gemini Batch did not return inline responses")
        normalized: list[dict[str, Any]] = []
        for item in rows:
            metadata = getattr(item, "metadata", None)
            request_id = metadata.get("request_id") if isinstance(metadata, dict) else None
            if not isinstance(request_id, str) or not request_id:
                raise BatchError("Gemini Batch response is missing request_id metadata")
            if getattr(item, "error", None) is not None:
                normalized.append({"request_id": request_id, "error_type": "provider_item_error"})
                continue
            response = getattr(item, "response", None)
            if response is None:
                normalized.append({"request_id": request_id, "error_type": "empty_provider_response"})
                continue
            calls: list[dict[str, Any]] = []
            for candidate in getattr(response, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    function_call = getattr(part, "function_call", None)
                    if function_call is not None:
                        calls.append(
                            {
                                "name": str(getattr(function_call, "name", "")),
                                "args": dict(getattr(function_call, "args", None) or {}),
                            }
                        )
            usage = getattr(response, "usage_metadata", None)
            prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) + int(
                getattr(usage, "tool_use_prompt_token_count", 0) or 0
            )
            output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
                getattr(usage, "thoughts_token_count", 0) or 0
            )
            normalized.append(
                {
                    "request_id": request_id,
                    "returned_model": self.model,
                    "received_at_ms": time.time_ns() // 1_000_000,
                    "function_calls": calls,
                    "input_tokens": prompt_tokens,
                    "output_tokens": output_tokens,
                }
            )
        return normalized
