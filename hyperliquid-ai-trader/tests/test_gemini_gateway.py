from __future__ import annotations

from types import SimpleNamespace
import json

from hyperliquid_ai_trader.gemini_gateway import GeminiGateway
import hyperliquid_ai_trader.gemini_gateway as gemini_gateway


class FakeModels:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    name="open_position",
                    args={
                        "side": "short",
                        "stop_loss_pct": 0.4,
                        "take_profit_pct": 0.7,
                        "confidence": 0.62,
                        "thesis": "book pressure",
                        "would_abstain": True,
                        "abstain_reason": "weak edge",
                    },
                )
            ]
        )


class FakeClient:
    def __init__(self) -> None:
        self.models = FakeModels()


class FakeReviewModels:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            parsed=None,
            text=json.dumps(
                {
                    "base_version": 1,
                    "summary": "no change",
                    "operations": [],
                }
            ),
        )


class FakeReviewClient:
    def __init__(self) -> None:
        self.models = FakeReviewModels()


def test_gemini_gateway_disables_automatic_execution_and_exposes_only_safe_tool() -> None:
    client = FakeClient()
    gateway = GeminiGateway(client=client)

    calls = gateway.generate_trade(prompt="prompt", model="gemini-test", temperature=0.7)

    assert calls[0].name == "open_position"
    assert calls[0].args["side"] == "short"
    assert client.models.kwargs is not None
    config = client.models.kwargs["config"]
    assert config.automatic_function_calling.disable is True
    declarations = config.tools[0].function_declarations
    assert [declaration.name for declaration in declarations] == ["open_position"]
    schema = declarations[0].parameters_json_schema
    assert "coin" not in schema["properties"]
    assert "size" not in schema["properties"]
    assert "leverage" not in schema["properties"]


def test_gemini_gateway_requests_structured_reviewer_patch() -> None:
    client = FakeReviewClient()
    gateway = GeminiGateway(client=client)

    patch = gateway.generate_review(prompt="review", model="gemini-review", temperature=0.4)

    assert patch == {"base_version": 1, "summary": "no change", "operations": []}
    assert client.models.kwargs is not None
    config = client.models.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["properties"]["operations"]["type"] == "array"


def test_gemini_client_uses_one_sdk_attempt_to_preserve_free_quota(monkeypatch) -> None:
    captured: dict = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(gemini_gateway.genai, "Client", fake_client)
    GeminiGateway(api_key="test-key")

    assert captured["http_options"].retry_options.attempts == 1
