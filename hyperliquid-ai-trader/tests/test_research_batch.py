import json
from decimal import Decimal
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research.batch import BatchError, BatchManager
from hyperliquid_ai_trader.research.config import load_research_config
from hyperliquid_ai_trader.research.request_identity import build_model_request
from hyperliquid_ai_trader.research.store import ResearchStore


class FakeProvider:
    def __init__(self, *, fail=False, sync_result=None, sync_error=None):
        self.fail = fail
        self.calls = 0
        self.sync_result = sync_result or []
        self.sync_error = sync_error

    def submit(self, payloads):
        self.calls += 1
        if self.fail:
            raise TimeoutError("submission outcome unknown")
        return "job-1"

    def sync(self, provider_job_id):
        if self.sync_error is not None:
            raise self.sync_error
        return self.sync_result


def _request(trial_id: str):
    return build_model_request(
        trial_id=trial_id,
        input_data={"market": {"as_of_ms": 300000}},
        constitution="c", instruction="i", strategy={}, requested_model="gemini-2.5-flash-lite",
        temperature=0, thinking="none", max_output_tokens=500,
        tool_schema={"name": "open_position"}, feature_set="common_candles_v1",
    )


def _paid_config(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    value = json.loads((root / "configs/research/development.json").read_text(encoding="utf-8"))
    value["api"]["allow_paid_api"] = True
    value["api"]["budget_usd"] = "1"
    path = tmp_path / "paid.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return load_research_config(path)


def test_paid_api_is_rejected_by_default(tmp_path: Path) -> None:
    config = load_research_config(Path(__file__).resolve().parents[1] / "configs/research/development.json")
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment(config.experiment_id, {}, 1)
        with pytest.raises(BatchError, match="allow_paid_api"):
            BatchManager().submit(config=config, requests=[_request("a")], store=store, provider=FakeProvider(), now_ms=2)


def test_submission_timeout_is_persisted_as_unknown_and_not_resent(tmp_path: Path) -> None:
    config = _paid_config(tmp_path)
    provider = FakeProvider(fail=True)
    request = _request("a")
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment(config.experiment_id, {}, 1)
        result = BatchManager().submit(config=config, requests=[request], store=store, provider=provider, now_ms=2)
        assert result.status == "submission_unknown"
        assert provider.calls == 1
        assert store.model_request(request.request_id)["status"] == "submission_unknown"


def test_submit_reserves_before_provider_call(tmp_path: Path) -> None:
    config = _paid_config(tmp_path)
    provider = FakeProvider()
    request = _request("a")
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment(config.experiment_id, {}, 1)
        BatchManager().submit(config=config, requests=[request], store=store, provider=provider, now_ms=2)
        assert provider.calls == 1
        assert store.model_request(request.request_id)["status"] == "submitted"


def test_completed_hash_reuse_is_not_counted_against_new_budget(tmp_path: Path) -> None:
    config = _paid_config(tmp_path)
    request = _request("a")
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment(config.experiment_id, {}, 1)
        store.prepare_model_request(
            experiment_id=config.experiment_id, request_id=request.request_id, trial_id=request.trial_id,
            request_hash=request.request_hash, canonical_payload=request.canonical_payload,
            reserved_cost_usd=Decimal("0.0005"), created_at_ms=1,
        )
        store.mark_submitted(request.request_id, provider_job_id="job", submitted_at_ms=2)
        store.mark_completed(request.request_id, completed_at_ms=3)
        result = BatchManager().submit(config=config, requests=[request], store=store, provider=FakeProvider(), now_ms=4)
        assert result.reused == 1 and result.submitted == 0


def test_sync_marks_provider_result_completed(tmp_path: Path) -> None:
    config = _paid_config(tmp_path)
    request = _request("a")
    provider = FakeProvider(sync_result=[{"request_id": request.request_id, "input_tokens": 2, "output_tokens": 3, "actual_cost_usd": "0.01"}])
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment(config.experiment_id, {}, 1)
        BatchManager().submit(config=config, requests=[request], store=store, provider=provider, now_ms=2)
        result = BatchManager().sync(store=store, provider=provider, now_ms=3)
        assert result.status == "completed"
        assert store.model_request(request.request_id)["status"] == "completed"


def test_sync_is_partial_until_all_submitted_requests_have_results(tmp_path: Path) -> None:
    config = _paid_config(tmp_path)
    requests = [_request("a"), _request("b")]
    provider = FakeProvider(sync_result=[{"request_id": requests[0].request_id}])
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment(config.experiment_id, {}, 1)
        BatchManager().submit(config=config, requests=requests, store=store, provider=provider, now_ms=2)
        result = BatchManager().sync(store=store, provider=provider, now_ms=3)
        assert result.status == "partial"
        assert store.model_request(requests[0].request_id)["status"] == "completed"
        assert store.model_request(requests[1].request_id)["status"] == "submitted"


def test_sync_communication_error_marks_submitted_requests_failed(tmp_path: Path) -> None:
    config = _paid_config(tmp_path)
    request = _request("a")
    provider = FakeProvider(sync_error=RuntimeError("network down"))
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment(config.experiment_id, {}, 1)
        BatchManager().submit(config=config, requests=[request], store=store, provider=FakeProvider(), now_ms=2)
        result = BatchManager().sync(store=store, provider=provider, now_ms=3)
        assert result.status == "failed"
        assert store.model_request(request.request_id)["status"] == "failed"
