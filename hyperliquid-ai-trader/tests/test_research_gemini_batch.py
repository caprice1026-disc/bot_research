from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from hyperliquid_ai_trader.research.gemini_batch import (
    GeminiBatchProvider,
    _gemini_open_position_schema,
)
from hyperliquid_ai_trader.research.request_identity import build_model_request


def _request():
    return build_model_request(
        trial_id="pilot-300000",
        input_data={"market": {"as_of_ms": 300000, "feature_set": "common_candles_v1"}},
        constitution="constitution",
        instruction="instruction",
        strategy={"version": 1, "active_rules": []},
        requested_model="gemini-3.5-flash",
        temperature=0,
        thinking="minimal",
        max_output_tokens=200,
        tool_schema={"name": "open_position", "parameters": {"type": "object"}},
        feature_set="common_candles_v1",
    )


class _FakeBatches:
    def __init__(self) -> None:
        self.create_args = None

    def create(self, **kwargs):
        self.create_args = kwargs
        return SimpleNamespace(name="batches/pilot")

    def get(self, *, name):
        assert name == "batches/pilot"
        call = SimpleNamespace(
            name="open_position",
            args={
                "side": "long",
                "stop_loss_pct": 0.3,
                "take_profit_pct": 0.6,
                "confidence": 0.5,
                "thesis": "fixture",
                "would_abstain": False,
                "abstain_reason": None,
            },
        )
        response = SimpleNamespace(
            model_version="gemini-3.5-flash",
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(function_call=call)]))],
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                tool_use_prompt_token_count=2,
                candidates_token_count=7,
                thoughts_token_count=0,
            ),
        )
        item = SimpleNamespace(
            metadata={"request_id": _request().request_id}, response=response, error=None
        )
        return SimpleNamespace(
            state="JOB_STATE_SUCCEEDED",
            dest=SimpleNamespace(inlined_responses=[item]),
        )


def test_gemini_function_schema_uses_the_supported_subset() -> None:
    schema = _gemini_open_position_schema()

    assert "additionalProperties" not in schema
    assert schema["properties"]["abstain_reason"]["type"] == "string"


def test_gemini_batch_preserves_request_identity_and_normalizes_function_call() -> None:
    client = SimpleNamespace(batches=_FakeBatches())
    request = _request()
    provider = GeminiBatchProvider(client=client, model="gemini-3.5-flash")

    assert provider.submit([request]) == "batches/pilot"

    sent = client.batches.create_args
    assert sent["model"] == "gemini-3.5-flash"
    assert sent["src"][0].metadata == {"request_id": request.request_id}
    assert sent["src"][0].config.max_output_tokens == 200
    assert sent["src"][0].config.thinking_config.thinking_level.value == "MINIMAL"

    result = provider.sync("batches/pilot")

    assert len(result) == 1
    assert result[0]["request_id"] == request.request_id
    assert result[0]["returned_model"] == "gemini-3.5-flash"
    assert result[0]["received_at_ms"] >= 300000
    assert result[0]["input_tokens"] == 12
    assert result[0]["output_tokens"] == 7
    assert result[0]["function_calls"] == [
        {
            "name": "open_position",
            "args": {
                "side": "long",
                "stop_loss_pct": 0.3,
                "take_profit_pct": 0.6,
                "confidence": 0.5,
                "thesis": "fixture",
                "would_abstain": False,
                "abstain_reason": None,
            },
        }
    ]


def test_gemini_batch_accepts_canonical_decimal_temperature() -> None:
    client = SimpleNamespace(batches=_FakeBatches())
    request = build_model_request(
        trial_id="pilot-decimal-temperature",
        input_data={"market": {"as_of_ms": 300000, "feature_set": "common_candles_v1"}},
        constitution="constitution",
        instruction="instruction",
        strategy={"version": 1, "active_rules": []},
        requested_model="gemini-3.5-flash",
        temperature=Decimal("0"),
        thinking="minimal",
        max_output_tokens=200,
        tool_schema={"name": "open_position", "parameters": {"type": "object"}},
        feature_set="common_candles_v1",
    )

    GeminiBatchProvider(client=client, model="gemini-3.5-flash").submit([request])

    assert client.batches.create_args["src"][0].config.temperature == 0.0
