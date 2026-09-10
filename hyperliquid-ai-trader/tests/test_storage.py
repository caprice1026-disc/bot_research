from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import uuid

from hyperliquid_ai_trader.storage import SQLiteStore


def _database_path() -> Path:
    directory = Path.cwd() / f"pytest-cache-files-storage-{uuid.uuid4().hex}"
    directory.mkdir()
    return directory / "trader.db"


def test_cycle_reservation_is_idempotent_across_restarts() -> None:
    path = _database_path()
    store = SQLiteStore(path)
    store.create_run(
        run_id="run-001",
        mode="dry_run",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )

    assert store.reserve_cycle("run-001", 0, scheduled_at_ms=1_000, strategy_version=1) is True
    assert store.reserve_cycle("run-001", 0, scheduled_at_ms=1_000, strategy_version=1) is False
    store.close()

    reopened = SQLiteStore(path)
    assert reopened.reserve_cycle("run-001", 0, scheduled_at_ms=1_000, strategy_version=1) is False
    reopened.close()


def test_store_persists_cycle_order_and_strategy_evidence() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-002",
        mode="testnet_live",
        started_at_ms=2_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="def456",
    )
    assert store.reserve_cycle("run-002", 3, scheduled_at_ms=3_000, strategy_version=1)
    store.complete_cycle(
        run_id="run-002",
        slot=3,
        status="ordered",
        features={"mid": 50000.0},
        decision={"side": "long", "confidence": 0.8},
        prompt_hash="prompt-hash",
        model="gemini-test",
        error_type=None,
    )
    store.record_order(
        run_id="run-002",
        slot=3,
        leg="entry",
        cloid="0x" + "a" * 32,
        status="filled",
        size=Decimal("0.005"),
        price=Decimal("50000"),
    )
    state = {
        "version": 1,
        "market_hypothesis": "initial hypothesis",
        "active_rules": [],
        "failure_modes": [],
        "confidence_calibration": {"long": 0.0, "short": 0.0},
        "last_review_cycle": 0,
    }
    store.save_strategy_version(
        run_id="run-002",
        version=1,
        parent_version=None,
        state=state,
        patch=None,
        model="bootstrap",
        created_at_ms=2_000,
    )
    store.record_patch(
        run_id="run-002",
        base_version=1,
        next_version=2,
        patch={"base_version": 1, "operations": []},
        accepted=True,
        reason="review accepted",
        created_at_ms=3_000,
    )

    assert store.known_cloids("run-002") == {"0x" + "a" * 32}
    assert store.load_latest_strategy("run-002") == state
    cycle = store.get_cycle("run-002", 3)
    assert cycle is not None
    assert cycle["status"] == "ordered"
    assert cycle["decision"]["side"] == "long"
    patch = store.connection.execute(
        "SELECT accepted, next_version FROM patches WHERE run_id='run-002'"
    ).fetchone()
    assert (patch["accepted"], patch["next_version"]) == (1, 2)
    store.close()


def test_store_records_validated_decision_before_risk_or_execution() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-decision",
        mode="dry_run",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )
    assert store.reserve_cycle("run-decision", 0, scheduled_at_ms=1_000, strategy_version=1)

    store.record_decision(
        run_id="run-decision",
        slot=0,
        arguments={"side": "long", "stop_loss_pct": "0.05"},
        prompt_hash="f" * 64,
        model="gemini-test",
        temperature=0.7,
        created_at_ms=1_100,
    )

    row = store.connection.execute(
        "SELECT * FROM decisions WHERE run_id='run-decision' AND slot=0"
    ).fetchone()
    assert row["model"] == "gemini-test"
    assert row["arguments_json"] == '{"side": "long", "stop_loss_pct": "0.05"}'
    store.close()


def test_slot_for_unmapped_close_fill_uses_latest_open_episode() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-close-slot",
        mode="testnet_live",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )
    assert store.reserve_cycle("run-close-slot", 0, scheduled_at_ms=1_000, strategy_version=1)
    assert store.reserve_cycle("run-close-slot", 1, scheduled_at_ms=301_000, strategy_version=1)
    store.complete_cycle(
        run_id="run-close-slot", slot=0, status="ordered", features={}, decision={"side": "long"},
        prompt_hash="a" * 64, model="test", error_type=None, completed_at_ms=2_000,
    )
    store.complete_cycle(
        run_id="run-close-slot", slot=1, status="reserved", features={}, decision=None,
        prompt_hash=None, model=None, error_type=None, completed_at_ms=0,
    )
    assert store.record_order(
        run_id="run-close-slot", slot=0, leg="entry", cloid="0x" + "a" * 32,
        status="filled", size=Decimal("0.005"), price=Decimal("50000"), oid=123,
    ) is None

    assert store.slot_for_unmapped_fill("run-close-slot", timestamp_ms=200_000) == 0
    store.record_fill(
        run_id="run-close-slot", slot=0, fill_id="close-0", side="long", size=Decimal("0.005"),
        price=Decimal("50100"), fee=Decimal("0.1"), closed_pnl=Decimal("0.5"), timestamp_ms=200_000,
    )
    assert store.slot_for_unmapped_fill("run-close-slot", timestamp_ms=400_000) is None
    store.close()


def test_slot_for_unmapped_close_fill_follows_latest_matching_entry_fill() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-close-entry-order",
        mode="testnet_live",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )
    for slot, scheduled_at_ms in ((0, 1_000), (1, 301_000)):
        assert store.reserve_cycle("run-close-entry-order", slot, scheduled_at_ms=scheduled_at_ms, strategy_version=1)
        store.complete_cycle(
            run_id="run-close-entry-order", slot=slot, status="ordered", features={},
            decision={"side": "short"}, prompt_hash="a" * 64, model="test",
            error_type=None, completed_at_ms=scheduled_at_ms,
        )
        store.record_fill(
            run_id="run-close-entry-order", slot=slot, fill_id=f"entry-{slot}", side="short",
            size=Decimal("0.005"), price=Decimal("50000"), fee=Decimal("0.1"),
            closed_pnl=Decimal("0"), timestamp_ms=scheduled_at_ms,
        )

    assert store.slot_for_unmapped_fill("run-close-entry-order", timestamp_ms=350_000, side="short") == 1
    store.record_fill(
        run_id="run-close-entry-order", slot=1, fill_id="close-1", side="short",
        size=Decimal("0.005"), price=Decimal("49900"), fee=Decimal("0.1"),
        closed_pnl=Decimal("0.5"), timestamp_ms=350_000,
    )
    assert store.slot_for_unmapped_fill("run-close-entry-order", timestamp_ms=400_000, side="short") == 0
    store.close()


def test_recent_closed_trades_includes_entry_fee_and_net_pnl() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-review-fees",
        mode="testnet_live",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )
    assert store.reserve_cycle("run-review-fees", 0, scheduled_at_ms=1_000, strategy_version=1)
    store.complete_cycle(
        run_id="run-review-fees", slot=0, status="ordered", features={}, decision={"side": "long"},
        prompt_hash="a" * 64, model="test", error_type=None, completed_at_ms=1_000,
    )
    store.record_fill(
        run_id="run-review-fees", slot=0, fill_id="entry-fee", side="long", size=Decimal("0.005"),
        price=Decimal("50000"), fee=Decimal("0.1"), closed_pnl=Decimal("0"), timestamp_ms=1_001,
    )
    store.record_fill(
        run_id="run-review-fees", slot=0, fill_id="close-fee", side="long", size=Decimal("0.005"),
        price=Decimal("50100"), fee=Decimal("0.2"), closed_pnl=Decimal("0.5"), timestamp_ms=2_000,
    )

    trades = store.recent_closed_trades("run-review-fees", limit=10)

    assert trades == [
        {
            "id": 2,
            "slot": 0,
            "side": "long",
            "size": "0.005",
            "price": "50100",
                "fee": "0.3",
                "gross_pnl": "0.5",
                "closed_pnl": "0.5",
                "net_pnl": "0.2",
            "timestamp_ms": 2_000,
        }
    ]
    store.close()


def test_record_event_deduplicates_identical_sync_events() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-events",
        mode="testnet_live",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )

    payload = {"oid": 123, "fill_id": "fill-1"}
    store.record_event(run_id="run-events", timestamp_ms=2_000, event_type="unmatched_fill", payload=payload)
    store.record_event(run_id="run-events", timestamp_ms=2_000, event_type="unmatched_fill", payload=payload)

    count = store.connection.execute(
        "SELECT COUNT(*) FROM events WHERE run_id=?", ("run-events",)
    ).fetchone()[0]
    assert count == 1
    store.close()


def test_closed_trade_context_includes_decision_and_new_trade_filter() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-review-context",
        mode="testnet_live",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )
    assert store.reserve_cycle("run-review-context", 0, scheduled_at_ms=1_000, strategy_version=1)
    store.complete_cycle(
        run_id="run-review-context", slot=0, status="ordered",
        features={"spread_bps": 4.0, "costs": {"estimated_round_trip_cost_bps": 13.0}},
        decision={"side": "long", "confidence": "0.8", "would_abstain": False, "thesis": "momentum"},
        prompt_hash="a" * 64, model="test", error_type=None, completed_at_ms=1_000,
    )
    store.record_fill(
        run_id="run-review-context", slot=0, fill_id="context-close", side="long",
        size=Decimal("0.005"), price=Decimal("50100"), fee=Decimal("0.2"),
        closed_pnl=Decimal("0.5"), timestamp_ms=2_000,
    )

    closed = store.recent_closed_trades("run-review-context", limit=10, include_context=True)
    assert closed[0]["decision"]["confidence"] == "0.8"
    assert closed[0]["features"]["costs"]["estimated_round_trip_cost_bps"] == 13.0
    assert store.new_closed_trades_since_review("run-review-context", closed) == closed

    store.record_review(
        run_id="run-review-context", review_index=1, created_at_ms=3_000,
        model="review", status="accepted_no_change",
        input_payload={"strategy": {}, "closed_trades": closed}, output_payload=None, error_type=None,
    )
    assert store.new_closed_trades_since_review("run-review-context", closed) == []
    store.close()


def test_new_closed_trades_union_all_prior_review_inputs_and_abstention_reference() -> None:
    store = SQLiteStore(_database_path())
    store.create_run(
        run_id="run-review-history",
        mode="testnet_live",
        started_at_ms=1_000,
        initial_equity=Decimal("1000"),
        initial_mark=Decimal("50000"),
        git_sha="abc123",
    )
    for slot in (0, 1):
        assert store.reserve_cycle("run-review-history", slot, scheduled_at_ms=1_000 + slot * 300_000, strategy_version=1)
    store.complete_cycle(
        run_id="run-review-history", slot=0, status="abstained",
        features={"mark": 50000, "costs": {"estimated_round_trip_cost_bps": 10}},
        decision={"side": "long", "would_abstain": True, "confidence": "0.4"},
        prompt_hash="a" * 64, model="test", error_type=None, completed_at_ms=1_000,
    )
    store.complete_cycle(
        run_id="run-review-history", slot=1, status="abstained",
        features={"mark": 50100, "costs": {"estimated_round_trip_cost_bps": 10}},
        decision={"side": "short", "would_abstain": True, "confidence": "0.4"},
        prompt_hash="b" * 64, model="test", error_type=None, completed_at_ms=301_000,
    )
    closed = [{"id": 1}, {"id": 2}, {"id": 3}]
    store.record_review(
        run_id="run-review-history", review_index=1, created_at_ms=2_000,
        model="review", status="accepted_no_change",
        input_payload={"new_closed_trades": [closed[0]]}, output_payload=None, error_type=None,
    )
    store.record_review(
        run_id="run-review-history", review_index=2, created_at_ms=3_000,
        model="review", status="skipped_no_new_trades",
        input_payload={"cumulative_closed_trades": [closed[0], closed[1]]}, output_payload=None, error_type=None,
    )

    assert store.new_closed_trades_since_review("run-review-history", closed) == [closed[2]]
    references = store.abstention_reference_outcomes("run-review-history", limit=10)
    assert [item["slot"] for item in references] == [0]
    assert references[0]["net_return_bps"] == "10.000000"
    assert references[0]["counterfactual"] is True
    assert store.new_abstention_reference_outcomes_since_review("run-review-history", references) == references
    store.record_review(
        run_id="run-review-history", review_index=3, created_at_ms=4_000,
        model="review", status="accepted_no_change",
        input_payload={"abstention_reference_outcomes": references}, output_payload=None, error_type=None,
    )
    assert store.new_abstention_reference_outcomes_since_review("run-review-history", references) == []
    store.close()
