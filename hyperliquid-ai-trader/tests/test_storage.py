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

    assert store.known_cloids("run-002") == {"0x" + "a" * 32}
    assert store.load_latest_strategy("run-002") == state
    cycle = store.get_cycle("run-002", 3)
    assert cycle is not None
    assert cycle["status"] == "ordered"
    assert cycle["decision"]["side"] == "long"
    store.close()
