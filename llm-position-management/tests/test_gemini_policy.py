import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from llm_position_management.gemini_policy import GeminiPolicy


def observation():
    return {"as_of_ms": 0, "position": {"signed_quantity": "0"}}


class FakeClient:
    def __init__(self, fail=False):
        self.models = self
        self.calls = 0
        self.fail = fail

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise ConnectionError("synthetic transport failure")
        return SimpleNamespace(text=json.dumps({"intent":"hold"}),
            usage_metadata=SimpleNamespace(prompt_token_count=100,candidates_token_count=10,thoughts_token_count=5))


def test_cached_response_has_no_second_charge_or_request(tmp_path):
    client = FakeClient()
    policy = GeminiPolicy(tmp_path,Decimal("1"),True,lambda t:{},client=client)
    first = policy.decide(observation(),"request:0")
    second = policy.decide(observation(),"request:0")
    assert first == second
    assert client.calls == 1
    assert first.estimated_cost_usd == Decimal("0.000285")
    assert first.received_at_ms > 0


def test_unknown_submission_keeps_reservation_and_cannot_be_retried(tmp_path):
    client = FakeClient(fail=True)
    policy = GeminiPolicy(tmp_path,Decimal("1"),True,lambda t:{},client=client)
    with pytest.raises(RuntimeError):
        policy.decide(observation(),"request:0")
    with pytest.raises(ValueError,match="resubmission"):
        policy.decide(observation(),"request:0")
    assert client.calls == 1
    assert policy.store.reserved_model_cost("budget") == Decimal("0.05")


def test_shared_budget_blocks_before_network_call(tmp_path):
    client = FakeClient()
    policy = GeminiPolicy(tmp_path,Decimal("0.04"),True,lambda t:{},client=client)
    with pytest.raises(ValueError,match="budget"):
        policy.decide(observation(),"request:0")
    assert client.calls == 0


def test_shared_atomic_reservations_cannot_exceed_budget(tmp_path):
    from llm_position_management.store import RunStore, StoreError
    first, second = RunStore(tmp_path/"shared.db"), RunStore(tmp_path/"shared.db")
    first.reserve_model_request("budget","a",Decimal("0.06"),budget_limit=Decimal("0.10"))
    with pytest.raises(StoreError,match="budget"):
        second.reserve_model_request("budget","b",Decimal("0.06"),budget_limit=Decimal("0.10"))
    assert first.reserved_model_cost("budget") == Decimal("0.06")


def test_schema_encodes_hold_nulls_and_no_resize_position_constraint():
    from llm_position_management.gemini_policy import response_schema
    schema = response_schema(False, has_position=True)
    assert len(schema["anyOf"]) == 2
    hold, flat = schema["anyOf"]
    assert hold["properties"]["target_fraction"] == {"type": "null"}
    assert hold["properties"]["stop_price"] == {"type": "null"}
    assert flat["properties"]["target_fraction"]["enum"] == ["0"]
    assert len(response_schema(True, has_position=True)["anyOf"]) == 3
