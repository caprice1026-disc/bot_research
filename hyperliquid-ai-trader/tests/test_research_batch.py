import json
from decimal import Decimal
from pathlib import Path

import pytest

from hyperliquid_ai_trader.research.batch import BatchError, BatchManager
from hyperliquid_ai_trader.research.config import load_research_config
from hyperliquid_ai_trader.research.request_identity import build_model_request
from hyperliquid_ai_trader.research.store import ResearchStore


class FakeProvider:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    def submit(self, payloads):
        self.calls += 1
        if self.fail:
            raise TimeoutError("submission outcome unknown")
        return "job-1"


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
