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
        self.kwargs = kwargs
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
    assert record["estimated_usd"] == "0.000375"
    assert client.kwargs["model"] == "gemini-3.6-flash"
    assert record["input_usd_per_million"] == "1.50"
    assert record["output_usd_per_million"] == "7.50"
    blocked = Gemini(replace(cfg, llm_budget_action="stop"), store, client_factory=lambda options: client, clock=lambda: 1800000000)
    with pytest.raises(BudgetExceeded):
        blocked.call("trader", "id2", {})
    assert client.calls == 1
    with pytest.raises(RuntimeError):
        policy.call("trader", "id", {})
    assert client.calls == 1


@pytest.mark.parametrize("payload", [
    {"intent": "hold", "target_multiple": None, "stop_price": None, "reason": "wait"},
    {"intent": "set_target", "target_multiple": "1.0", "stop_price": "49000", "reason": "long"},
    {"intent": "set_target", "target_multiple": "0.0", "stop_price": None, "reason": "flat"},
])
def test_gemini_36_decisions_keep_schema_and_risk_validation(tmp_path, payload):
    import json
    from position_stg.domain import account, plan
    from position_stg.policy import TRADER_SCHEMA

    cfg = Config()
    store = SQLiteStore(tmp_path / "state.db")
    client = FakeClient()
    original = client.generate_content
    def respond(**kwargs):
        response = original(**kwargs)
        response.text = json.dumps(payload)
        return response
    client.generate_content = respond
    options_seen = []
    def factory(options):
        options_seen.append(options)
        return client
    policy = Gemini(cfg, store, client_factory=factory)
    result = policy.call("trader", "decision", {})
    config = client.kwargs["config"]
    assert client.kwargs["model"] == "gemini-3.6-flash"
    assert options_seen[0].timeout == 60000
    assert options_seen[0].retry_options.attempts == 1
    assert config.response_json_schema == TRADER_SCHEMA
    assert config.response_mime_type == "application/json"
    assert config.max_output_tokens == 2048
    assert config.automatic_function_calling.disable
    assert not config.tools
    planned = plan(account(cfg.initial_equity), "50000", ".00001", result, cfg)
    assert D(planned["target"]) >= 0
    assert client.calls == 1


def test_deadline_failure_retains_reservation_and_cannot_resubmit(tmp_path, monkeypatch):
    cfg = Config()
    store = SQLiteStore(tmp_path / "state.db")
    client = FakeClient()
    original = client.generate_content
    mono = [100.0]
    monkeypatch.setattr("position_stg.policy.time.monotonic", lambda: mono[0])
    def delayed(**kwargs):
        mono[0] += 61
        return original(**kwargs)
    client.generate_content = delayed
    policy = Gemini(cfg, store, client_factory=lambda options: client)
    with pytest.raises(TimeoutError):
        policy.call("trader", "late", {})
    assert store.read("llm-late")[0]["status"] == "timeout"
    with pytest.raises(RuntimeError):
        policy.call("trader", "late", {})
    assert client.calls == 1


def test_transport_failure_retains_budget_and_blocks_resubmission(tmp_path):
    cfg = Config()
    store = SQLiteStore(tmp_path / "state.db")
    client = FakeClient()
    def fail(**kwargs):
        client.calls += 1
        raise TimeoutError()
    client.generate_content = fail
    policy = Gemini(cfg, store, client_factory=lambda options: client)
    with pytest.raises(TimeoutError):
        policy.call("trader", "unknown", {})
    record = store.read("llm-unknown")[0]
    assert D(record["estimated_usd"]) > 0
    with pytest.raises(RuntimeError):
        policy.call("trader", "unknown", {})
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
