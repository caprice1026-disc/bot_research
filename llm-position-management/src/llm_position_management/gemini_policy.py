"""Single-attempt Gemini policy with durable shared budget and response journal."""
import hashlib
import json
import math
import os
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from .policy import PolicyResponse
from .store import RunStore


PROMPT = """You manage one simulated BTC perpetual position. Reassess every five minutes;
do not close merely because five minutes elapsed. Use ONLY the supplied past/current
market and account observations. No browsing or remembered historical outcomes.
Choose hold when there is no defensible cost-adjusted opportunity. A nonzero target
needs a protective stop; do not loosen an existing stop. Never reverse without first
going flat. The risk engine owns size limits, cash and execution. Return only the
specified JSON. Reasons must be brief, grounded in supplied features. Equity is USDT.
Fees are 0.045% per fill plus 2 bps spread and 1 bp slippage. A target fraction is of a
fixed 250 USD exposure anchor, not equity. Model costs count against the result.
If resize is false and a position already exists, choose only hold or target zero.
For hold, both target_fraction and stop_price MUST be null. For target zero, stop_price
MUST be null. For a nonzero target, stop_price MUST be a positive decimal string.
"""


def response_schema(resize, has_position=False):
    fractions = ["-1", "0", "1"]
    if resize:
        fractions += ["-0.5", "-0.25", "0.25", "0.5"]
    base = {"type": "object", "additionalProperties": False, "properties": {
        "schema_version": {"type": "integer", "enum": [1]}, "decision_id": {"type": "string"},
        "intent": {"type": "string", "enum": ["hold", "set_target"]},
        "target_fraction": {"type": ["string", "null"], "enum": fractions+[None]},
        "stop_price": {"type": ["string", "null"]}, "thesis": {"type": "string"},
        "invalidation": {"type": "string"}},
        "required": ["schema_version", "decision_id", "intent", "target_fraction", "stop_price", "thesis", "invalidation"]}
    branches = []
    for kind in ("hold", "flat", "position"):
        if kind == "position" and not resize and has_position:
            continue
        branch = json.loads(json.dumps(base))
        properties = branch["properties"]
        properties["intent"] = {"type": "string", "enum": ["hold" if kind == "hold" else "set_target"]}
        properties["target_fraction"] = ({"type": "null"} if kind == "hold" else
            {"type": "string", "enum": ["0"] if kind == "flat" else [value for value in fractions if value != "0"]})
        properties["stop_price"] = {"type": "string"} if kind == "position" else {"type": "null"}
        branches.append(branch)
    return {"anyOf": branches}


class GeminiPolicy:
    reservation = Decimal("0.05")

    def __init__(self, directory: Path, budget: Decimal, resize: bool, features, *, client=None):
        self.directory, self.resize, self.features = directory, resize, features
        directory.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.store = RunStore(directory / "budget.db")
        self.store.begin("budget", f"gemini-3.5-flash:standard:1.5:9:minimal:512:{budget}")
        self.client = client

    def reserve_cost_usd(self, observation, request_id):
        return self.reservation

    def decide(self, observation, request_id):
        name = hashlib.sha256(request_id.encode()).hexdigest()
        response_path = self.directory / f"{name}.json"
        request = {"decision_id": request_id, "observation": observation,
                   "features": self.features(observation["as_of_ms"]), "resize": self.resize}
        prompt = PROMPT + json.dumps(request, sort_keys=True)
        request_hash = hashlib.sha256(prompt.encode()).hexdigest()
        if response_path.exists():
            saved = json.loads(response_path.read_text(encoding="utf-8"))
            if saved["request_hash"] != request_hash:
                raise ValueError("cached response request mismatch")
            return PolicyResponse(saved["payload"], saved["received_at_ms"], Decimal(saved["cost"]))
        if request_id in self.store.claimed_ids("budget"):
            raise ValueError("request outcome unknown; automatic resubmission forbidden")
        schema = response_schema(self.resize, Decimal(observation["position"]["signed_quantity"]) != 0)
        # Byte bound plus ample schema/protocol overhead bounds the input tokens.
        if len(prompt.encode()) + len(json.dumps(schema).encode()) > 10_000:
            raise ValueError("prompt exceeds bounded input size")
        if self.store.total_model_cost("budget") + self.store.reserved_model_cost("budget") + self.reservation > self.budget:
            raise ValueError("shared API budget exhausted")
        from google import genai
        from google.genai import types
        if self.client is None:
            from dotenv import dotenv_values
            key = os.environ.get("GEMINI_API_KEY") or dotenv_values(".env").get("GEMINI_API_KEY")
            if not key:
                raise ValueError("Gemini API key unavailable")
            self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30_000,
                client_args={"trust_env": False}, retry_options=types.HttpRetryOptions(attempts=1)))
        self.store.reserve_model_request("budget", request_id, self.reservation, budget_limit=self.budget)
        started = perf_counter()
        try:
            response = self.client.models.generate_content(model="gemini-3.5-flash", contents=prompt,
                config=types.GenerateContentConfig(temperature=0, max_output_tokens=512,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                    response_mime_type="application/json", response_json_schema=schema,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
        except Exception:
            self.store.mark_model_request_unknown("budget", request_id)
            raise RuntimeError("Gemini attempt failed; reservation retained") from None
        latency_ms = max(1, math.ceil((perf_counter()-started)*1000))
        usage = response.usage_metadata
        if usage is None:
            self.store.mark_model_request_unknown("budget", request_id)
            raise RuntimeError("missing usage; reservation retained")
        input_tokens = usage.prompt_token_count or 0
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        cost = (Decimal(input_tokens)*Decimal("1.5") + Decimal(output_tokens)*9)/1_000_000
        try:
            payload = json.loads(response.text or "")
            if not isinstance(payload, dict):
                payload = None
        except (ValueError, TypeError):
            payload = None
        raw_payload = payload
        validation_error = None
        if (not self.resize and Decimal(observation["position"]["signed_quantity"]) != 0
                and payload and payload.get("intent") == "set_target"
                and payload.get("target_fraction") != "0"):
            payload = None
            validation_error = "no_resize_position_must_hold_or_close"
        saved = {"request_hash": request_hash, "request": request, "payload": payload,
                 "raw_payload": raw_payload, "validation_error": validation_error,
                 "received_at_ms": int(observation["as_of_ms"])+latency_ms,
                 "latency_ms": latency_ms, "input_tokens": input_tokens,
                 "output_tokens_including_thinking": output_tokens, "cost": str(cost)}
        temporary = response_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(saved, sort_keys=True), encoding="utf-8")
        temporary.replace(response_path)
        self.store.settle_model_request("budget", request_id, cost)
        return PolicyResponse(payload, saved["received_at_ms"], cost)
