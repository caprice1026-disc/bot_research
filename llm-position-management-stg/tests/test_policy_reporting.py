from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from position_stg.config import Config
from position_stg.policy import Gemini, BudgetExceeded, validate_review
from position_stg.store import SQLiteStore
from position_stg.reporting import archive, send_once


class FakeClient:
    def __init__(self):
        self.models = self
        self.calls = 0
    def close(self):
        pass
    def generate_content(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(text='{"intent":"hold","target_multiple":null,"stop_price":null,"reason":"test"}',
            usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=10, thoughts_token_count=20), model_version="returned-version")


def test_budget_notify_continues_and_call_records_thinking_latency(tmp_path):
    cfg = replace(Config(), llm_monthly_budget_usd=D(0))
    store = SQLiteStore(tmp_path / "state.db")
    client = FakeClient()
    policy = Gemini(cfg, store, client_factory=lambda options: client, clock=lambda: 1800000000)
    assert policy.call("trader", "id", {})["intent"] == "hold"
    record, _ = store.read("llm-id")
    assert record["output_tokens"] == 30 and record["latency_seconds"] >= 0
    assert record["estimated_usd"] == "0.00042"
    with pytest.raises(RuntimeError):
        policy.call("trader", "id", {})
    assert client.calls == 1
    blocked = Gemini(replace(cfg, llm_budget_action="stop"), store, client_factory=lambda options: client, clock=lambda: 1800000000)
    with pytest.raises(BudgetExceeded):
        blocked.call("trader", "id2", {})
    assert client.calls == 1


def test_reviewer_cannot_change_risk_or_invent_sources():
    valid = {"market_hypothesis": "x", "active_rules": [], "failure_modes": [], "change_reason": "x", "evidence_ids": []}
    assert validate_review(valid, []) == valid
    with pytest.raises(ValueError):
        validate_review({**valid, "max_exposure": 99}, [])
    with pytest.raises(ValueError):
        validate_review({**valid, "evidence_ids": ["invented"]}, [])


def test_discord_unknown_delivery_not_blindly_retried(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    class Session:
        calls = 0
        def post(self, *a, **kw):
            self.calls += 1
            assert kw["json"]["allowed_mentions"] == {"parse": []}
            raise TimeoutError()
    session = Session()
    with pytest.raises(RuntimeError):
        send_once(store, "https://discord.invalid/webhook", "notice-test", "x", session=session)
    send_once(store, "https://discord.invalid/webhook", "notice-test", "x", session=session)
    assert session.calls == 1


def test_archive_failure_retains_source_and_success_verifies(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.write("cycle-old", {"at": 1, "status": "complete"}, 0)
    class Blob:
        payload = b""
        corrupt = True
        def exists(self): return bool(self.payload)
        def upload_from_string(self, data, **kwargs): self.payload = data
        def download_as_bytes(self): return b"bad" if self.corrupt else self.payload
    blob = Blob()
    bucket = SimpleNamespace(blob=lambda name: blob)
    with pytest.raises(ValueError):
        archive(store, bucket, "bot", now=4000000)
    assert store.read("cycle-old")[0] is not None
    blob.corrupt = False
    assert archive(store, bucket, "bot", now=4000000) == 1
    assert store.read("cycle-old")[0] is None
