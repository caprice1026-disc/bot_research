"""Real Google SDK serialization with locally intercepted HTTP; no exchange client."""
import json
from decimal import Decimal

import httpx
import pytest
from google import genai

from position_stg.config import Config
from position_stg.domain import account, plan
from position_stg.policy import Gemini, TRADER_SCHEMA
from position_stg.store import SQLiteStore


@pytest.mark.parametrize("payload", [
    {"intent": "hold", "target_multiple": None, "stop_price": None, "reason": "wait"},
    {"intent": "set_target", "target_multiple": "1.0", "stop_price": "49000", "reason": "long"},
    {"intent": "set_target", "target_multiple": "0.0", "stop_price": None, "reason": "flat"},
])
def test_sdk_sends_gemini_36_schema_and_keeps_decision_contract(tmp_path, payload):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "modelVersion": "gemini-3.6-flash",
            "candidates": [{"index": 0, "finishReason": "STOP", "content": {
                "role": "model", "parts": [{"text": json.dumps(payload)}],
            }}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10,
                              "thoughtsTokenCount": 20, "totalTokenCount": 130},
        })
    def factory(options):
        assert options.timeout == 60000
        assert options.retry_options.attempts == 1
        options.async_client_args = {"trust_env": False}
        options.httpx_client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
        return genai.Client(api_key="fake-key", http_options=options)
    cfg = Config()
    store = SQLiteStore(tmp_path / "state.db")
    result = Gemini(cfg, store, client_factory=factory).call("trader", "wire", {})
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/models/gemini-3.6-flash:generateContent")
    body = json.loads(requests[0].content)
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == TRADER_SCHEMA
    assert config["maxOutputTokens"] == 2048
    thinking = config["thinkingConfig"]
    assert thinking.get("thinkingLevel", thinking.get("thinking_level")) == "LOW"
    assert not body.get("tools")
    assert plan(account(cfg.initial_equity), "50000", ".00001", result, cfg)
    record = store.read("llm-wire")[0]
    assert record["returned_model"] == "gemini-3.6-flash"
    assert record["output_tokens"] == 30
    assert Decimal(record["estimated_usd"]) == Decimal("0.000375")
