from __future__ import annotations

from hyperliquid_ai_trader.research.store import ResearchStore


def test_zero_pnl_and_split_close_become_evidence_once(tmp_path) -> None:
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment("exp", {"network": "mainnet"}, 1)
        store.record_episode(
            episode_id="trade:d1", experiment_id="exp", decision_id="d1", kind="trade",
            entry_quantity="2", exit_quantity="1", gross="0", fee="0.1", funding="0",
            net="-0.1", quality="complete", closed_at_ms=2, payload={},
        )
        assert store.confirmed_evidence("exp") == []

        store.record_episode(
            episode_id="trade:d2", experiment_id="exp", decision_id="d2", kind="trade",
            entry_quantity="2", exit_quantity="2", gross="0", fee="0.1", funding="0",
            net="-0.1", quality="complete", closed_at_ms=3, payload={},
        )
        assert store.unevaluated_evidence_ids("exp") == ["trade:d2"]
        store.finish_review(review_id="failed", experiment_id="exp", cutoff_ms=4,
                            status="failed", evidence_ids=["trade:d2"])
        assert store.unevaluated_evidence_ids("exp") == ["trade:d2"]
        store.finish_review(review_id="ok", experiment_id="exp", cutoff_ms=5,
                            status="validated_no_change", evidence_ids=["trade:d2"])
        assert store.unevaluated_evidence_ids("exp") == []
