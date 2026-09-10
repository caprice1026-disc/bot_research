from __future__ import annotations

import pytest

from hyperliquid_ai_trader.research.store import ResearchStore, ResearchStoreError


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


def test_numerically_equal_quantities_close_an_episode_once(tmp_path) -> None:
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment("exp", {"network": "mainnet"}, 1)
        store.record_episode(
            episode_id="trade:decimal", experiment_id="exp", decision_id="d1", kind="trade",
            entry_quantity="2.0", exit_quantity="2.000", gross="0", fee="0.1", funding="0",
            net="-0.1", quality="complete", closed_at_ms=2, payload={},
        )

        assert [row["episode_id"] for row in store.confirmed_evidence("exp")] == ["trade:decimal"]


def test_review_cannot_consume_future_or_other_experiment_evidence(tmp_path) -> None:
    with ResearchStore(tmp_path / "research.db") as store:
        store.create_experiment("a", {"network": "mainnet"}, 1)
        store.create_experiment("b", {"network": "mainnet"}, 1)
        store.record_episode(
            episode_id="trade:a", experiment_id="a", decision_id="d1", kind="trade",
            entry_quantity="1", exit_quantity="1", gross="0", fee="0", funding="0",
            net="0", quality="complete", closed_at_ms=10, payload={},
        )

        with pytest.raises(ResearchStoreError, match="not eligible"):
            store.finish_review(
                review_id="future", experiment_id="a", cutoff_ms=9,
                status="validated_no_change", evidence_ids=["trade:a"],
            )
        with pytest.raises(ResearchStoreError, match="not eligible"):
            store.finish_review(
                review_id="other", experiment_id="b", cutoff_ms=10,
                status="validated_no_change", evidence_ids=["trade:a"],
            )
