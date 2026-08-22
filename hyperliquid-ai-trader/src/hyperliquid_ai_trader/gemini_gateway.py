"""Official google-genai client configured for non-executing Function Calling."""

from __future__ import annotations

import json
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from .agents import FunctionCall, ModelGatewayError


_OPEN_POSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["long", "short"]},
        "stop_loss_pct": {"type": "number"},
        "take_profit_pct": {"type": "number"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "thesis": {"type": "string"},
        "would_abstain": {"type": "boolean"},
        "abstain_reason": {"type": ["string", "null"]},
    },
    "required": [
        "side",
        "stop_loss_pct",
        "take_profit_pct",
        "confidence",
        "thesis",
        "would_abstain",
        "abstain_reason",
    ],
    "additionalProperties": False,
}

_STRATEGY_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "base_version": {"type": "integer"},
        "summary": {"type": "string"},
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "remove", "replace"]},
                    "path": {"type": "string"},
                    "value": {},
                    "evidence": {
                        "type": "object",
                        "properties": {
                            "trade_ids": {"type": "array", "items": {"type": "integer"}},
                            "net_pnl": {"type": "number"},
                            "note": {"type": "string"},
                        },
                        "required": ["trade_ids"],
                    },
                },
                "required": ["op", "path", "value", "evidence"],
            },
        },
    },
    "required": ["base_version", "summary", "operations"],
    "additionalProperties": False,
}


class GeminiGateway:
    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        if client is not None:
            self.client = client
        elif api_key:
            self.client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=30_000,
                    client_args={"trust_env": False},
                    async_client_args={"trust_env": False},
                ),
            )
        else:
            raise ValueError("api_key or client is required")

    def validate_model(self, model: str) -> None:
        try:
            self.client.models.get(model=model)
        except errors.APIError as exc:
            raise ModelGatewayError(
                "model_unavailable",
                retryable=getattr(exc, "code", 0) in {429, 500, 502, 503, 504},
            ) from exc

    def generate_trade(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> list[FunctionCall]:
        declaration = types.FunctionDeclaration(
            name="open_position",
            description=(
                "Choose exactly one LONG or SHORT trade. Position size, coin, leverage, "
                "account, and network are controlled by the external risk engine."
            ),
            parameters_json_schema=_OPEN_POSITION_SCHEMA,
        )
        config = types.GenerateContentConfig(
            temperature=temperature,
            tools=[types.Tool(function_declarations=[declaration])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=["open_position"],
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except errors.APIError as exc:
            code = getattr(exc, "code", 0)
            error_type = "rate_limited" if code == 429 else "model_api_error"
            raise ModelGatewayError(
                error_type,
                retryable=code in {429, 500, 502, 503, 504},
            ) from exc
        except httpx.TransportError as exc:
            raise ModelGatewayError("model_transport_error", retryable=True) from exc

        calls = response.function_calls or []
        return [
            FunctionCall(name=str(call.name or ""), args=dict(call.args or {}))
            for call in calls
        ]

    def generate_review(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
    ) -> dict[str, Any]:
        config = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            response_json_schema=_STRATEGY_PATCH_SCHEMA,
        )
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except errors.APIError as exc:
            code = getattr(exc, "code", 0)
            error_type = "rate_limited" if code == 429 else "model_api_error"
            raise ModelGatewayError(
                error_type,
                retryable=code in {429, 500, 502, 503, 504},
            ) from exc
        except httpx.TransportError as exc:
            raise ModelGatewayError("model_transport_error", retryable=True) from exc

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, dict):
            return parsed
        try:
            value = json.loads(response.text or "")
        except (json.JSONDecodeError, TypeError) as exc:
            raise ModelGatewayError("invalid_model_json", retryable=True) from exc
        if not isinstance(value, dict):
            raise ModelGatewayError("invalid_model_json", retryable=True)
        return value
