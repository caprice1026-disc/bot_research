from __future__ import annotations

from decimal import Decimal

import pytest

from hyperliquid_ai_trader.research.request_identity import build_model_request
from hyperliquid_ai_trader.research.store import ResearchStore, ResearchStoreError


def _request(*, trial_id: str = "trial-a", model: str = "gemini-2.5-flash-lite"):
    return build_model_request(
        trial_id=trial_id,
        input_data={"market": {"return_5m": 0.01, "return_1m": 0.002}},
        constitution="fixed constitution",
        instruction="fixed trader instruction",
        strategy={"version": 1, "rules": ["fixture"]},
        requested_model=model,
        temperature=0,
        thinking="none",
        max_output_tokens=500,
        tool_schema={"name": "open_position", "parameters": {"type": "object"}},
        feature_set="common_candles_v1",
    )


def test_request_hash_is_canonical_but_trial_id_keeps_independent_attempts_distinct() -> None:
    first = _request(trial_id="trial-a")
    same_input_new_trial = _request(trial_id="trial-b")
    different_model = _request(trial_id="trial-a", model="gemini-3.6-flash")

    assert first.request_hash == same_input_new_trial.request_hash
    assert first.request_id != same_input_new_trial.request_id
    assert first.request_hash != different_model.request_hash
    assert '"requested_model":"gemini-2.5-flash-lite"' in first.canonical_payload


def test_request_reservation_persists_unknown_submission_without_auto_resend(tmp_path) -> None:
    request = _request()
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment("exp", {"network": "mainnet"}, 1)
        store.prepare_model_request(
            experiment_id="exp",
            request_id=request.request_id,
            trial_id=request.trial_id,
            request_hash=request.request_hash,
            canonical_payload=request.canonical_payload,
            reserved_cost_usd=Decimal("0.25"),
            created_at_ms=2,
        )
        store.mark_submission_unknown(request.request_id)

        record = store.model_request(request.request_id)
        assert record["status"] == "submission_unknown"
        assert record["reserved_cost_usd"] == "0.25"
        with pytest.raises(ResearchStoreError, match="prepared"):
            store.mark_submission_unknown(request.request_id)
