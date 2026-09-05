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
