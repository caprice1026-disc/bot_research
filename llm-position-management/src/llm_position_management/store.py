"""Small SQLite persistence for offline sequential runs and safe restart checks."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any

from trading_core.accounting.models import AccountSnapshot
from trading_core.market_data.models import MarketTick
from trading_core.simulation.position_account import AccountEvent

if TYPE_CHECKING:
    from .runner import DecisionRecord


class StoreError(ValueError):
    """Raised when persisted state cannot safely be reused."""


@dataclass(frozen=True)
class StoredRunState:
    snapshot: AccountSnapshot
    tick: MarketTick
    consecutive_failures: int
    model_cost_usd: Decimal


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _snapshot_from_json(payload: dict[str, Any]) -> AccountSnapshot:
    decimal_fields = {
        "signed_quantity", "cash", "equity", "unrealized_pnl", "day_start_equity",
        "daily_realized_pnl", "peak_equity", "realized_pnl", "fees_paid", "funding_paid",
    }
    values = dict(payload)
    for field in decimal_fields:
        values[field] = Decimal(values[field])
    for field in ("average_entry_price", "stop_price"):
        if values[field] is not None:
            values[field] = Decimal(values[field])
    values["pending_order_ids"] = tuple(values["pending_order_ids"])
    return AccountSnapshot(**values)


def _tick_from_json(payload: dict[str, Any]) -> MarketTick:
    return MarketTick(
        timestamp_ms=int(payload["timestamp_ms"]),
        open_price=Decimal(payload["open_price"]),
        high_price=Decimal(payload["high_price"]),
        low_price=Decimal(payload["low_price"]),
        close_price=Decimal(payload["close_price"]),
    )


def _event_from_json(payload: dict[str, Any]) -> AccountEvent:
    values = dict(payload)
    values["quantity"] = Decimal(values["quantity"])
    values["amount"] = Decimal(values["amount"])
    if values["price"] is not None:
        values["price"] = Decimal(values["price"])
    return AccountEvent(**values)


class RunStore:
    """One local run database in WAL mode; it does not share any live trader DB."""

    _SCHEMA_VERSION = 2

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, self._SCHEMA_VERSION}:
                raise StoreError(f"unsupported run DB schema version: {version}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, config_fingerprint TEXT NOT NULL)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS decisions (
                    run_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    model_cost_usd TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, decision_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS snapshots (
                    run_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    tick_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, decision_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runner_state (
                    run_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    tick_json TEXT NOT NULL,
                    consecutive_failures INTEGER NOT NULL,
                    model_cost_usd TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS account_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    event_json TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS model_requests (
                    run_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    reserved_cost_usd TEXT NOT NULL,
                    actual_cost_usd TEXT,
                    status TEXT NOT NULL,
                    PRIMARY KEY (run_id, request_id)
                )"""
            )
            connection.execute(f"PRAGMA user_version={self._SCHEMA_VERSION}")

    @contextmanager
    def _connect(self):
        """Commit on success and always release SQLite/WAL handles on Windows."""

        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(_plain(asdict(value)), sort_keys=True, separators=(",", ":"))

    def _write_state(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        snapshot: AccountSnapshot,
        tick: MarketTick,
        *,
        consecutive_failures: int,
        model_cost_usd: Decimal,
    ) -> None:
        if consecutive_failures < 0:
            raise StoreError("consecutive failures cannot be negative")
        connection.execute(
            """INSERT INTO runner_state VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                   snapshot_json = excluded.snapshot_json,
                   tick_json = excluded.tick_json,
                   consecutive_failures = excluded.consecutive_failures,
                   model_cost_usd = excluded.model_cost_usd""",
            (run_id, self._json(snapshot), self._json(tick), consecutive_failures, str(model_cost_usd)),
        )

    def _write_events(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        events: tuple[AccountEvent, ...],
    ) -> None:
        connection.executemany(
            "INSERT INTO account_events (run_id, timestamp_ms, event_json) VALUES (?, ?, ?)",
            [(run_id, event.timestamp_ms, self._json(event)) for event in events],
        )

    def begin(self, run_id: str, config_fingerprint: str) -> None:
        if not run_id or not config_fingerprint:
            raise StoreError("run ID and config fingerprint are required")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT config_fingerprint FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO runs (run_id, config_fingerprint) VALUES (?, ?)",
                    (run_id, config_fingerprint),
                )
            elif row[0] != config_fingerprint:
                raise StoreError("run config fingerprint does not match persisted state")

    def decision_ids(self, run_id: str) -> set[str]:
        with self._connect() as connection:
            return {row[0] for row in connection.execute("SELECT decision_id FROM decisions WHERE run_id = ?", (run_id,))}

    def claimed_ids(self, run_id: str) -> set[str]:
        """Return slots with a saved result or an externally submitted unknown request."""

        with self._connect() as connection:
            decision_ids = {row[0] for row in connection.execute("SELECT decision_id FROM decisions WHERE run_id = ?", (run_id,))}
            request_ids = {row[0] for row in connection.execute("SELECT request_id FROM model_requests WHERE run_id = ?", (run_id,))}
        return decision_ids | request_ids

    def total_model_cost(self, run_id: str) -> Decimal:
        """Actual settled model costs plus legacy decision rows from schema v1."""

        with self._connect() as connection:
            requested = connection.execute(
                "SELECT actual_cost_usd FROM model_requests WHERE run_id = ? AND actual_cost_usd IS NOT NULL",
                (run_id,),
            ).fetchall()
            legacy = connection.execute(
                """SELECT d.model_cost_usd FROM decisions d
                   WHERE d.run_id = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM model_requests r
                         WHERE r.run_id = d.run_id AND r.request_id = d.decision_id
                     )""",
                (run_id,),
            ).fetchall()
        return sum((Decimal(row[0]) for row in (*requested, *legacy)), Decimal("0"))

    def reserved_model_cost(self, run_id: str) -> Decimal:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT reserved_cost_usd FROM model_requests
                   WHERE run_id = ? AND status IN ('reserved', 'submission_unknown')""",
                (run_id,),
            )
            return sum((Decimal(row[0]) for row in rows), Decimal("0"))

    def reserve_model_request(self, run_id: str, request_id: str, reserved_cost_usd: Decimal) -> None:
        if not reserved_cost_usd.is_finite() or reserved_cost_usd < 0:
            raise StoreError("reserved model cost must be finite and non-negative")
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO model_requests VALUES (?, ?, ?, NULL, 'reserved')",
                    (run_id, request_id, str(reserved_cost_usd)),
                )
        except sqlite3.IntegrityError as error:
            raise StoreError(f"model request is already reserved: {request_id}") from error

    def settle_model_request(self, run_id: str, request_id: str, actual_cost_usd: Decimal) -> None:
        if not actual_cost_usd.is_finite() or actual_cost_usd < 0:
            raise StoreError("actual model cost must be finite and non-negative")
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE model_requests
                   SET actual_cost_usd = ?, status = 'settled'
                   WHERE run_id = ? AND request_id = ? AND status = 'reserved'""",
                (str(actual_cost_usd), run_id, request_id),
            ).rowcount
            if updated != 1:
                raise StoreError(f"model request cannot be settled: {request_id}")

    def mark_model_request_unknown(self, run_id: str, request_id: str) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE model_requests SET status = 'submission_unknown'
                   WHERE run_id = ? AND request_id = ? AND status = 'reserved'""",
                (run_id, request_id),
            ).rowcount
            if updated != 1:
                raise StoreError(f"model request cannot be marked unknown: {request_id}")

    def load_state(self, run_id: str) -> StoredRunState | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT snapshot_json, tick_json, consecutive_failures, model_cost_usd
                   FROM runner_state WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT snapshot_json, tick_json, 0, '0' FROM snapshots
                       WHERE run_id = ? ORDER BY timestamp_ms DESC, rowid DESC LIMIT 1""",
                    (run_id,),
                ).fetchone()
        if row is None:
            return None
        try:
            return StoredRunState(
                snapshot=_snapshot_from_json(json.loads(row[0])),
                tick=_tick_from_json(json.loads(row[1])),
                consecutive_failures=int(row[2]),
                model_cost_usd=Decimal(row[3]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StoreError("persisted run state is invalid") from error

    def load_latest(self, run_id: str) -> tuple[AccountSnapshot, MarketTick] | None:
        """Compatibility accessor for callers that only need account and market state."""

        state = self.load_state(run_id)
        return None if state is None else (state.snapshot, state.tick)

    def account_events(self, run_id: str) -> tuple[AccountEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM account_events WHERE run_id = ? ORDER BY event_id", (run_id,)
            ).fetchall()
        try:
            return tuple(_event_from_json(json.loads(row[0])) for row in rows)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StoreError("persisted account event is invalid") from error

    def save_state(
        self,
        run_id: str,
        snapshot: AccountSnapshot,
        tick: MarketTick,
        *,
        consecutive_failures: int,
        model_cost_usd: Decimal,
        events: tuple[AccountEvent, ...] = (),
    ) -> None:
        with self._connect() as connection:
            self._write_state(
                connection,
                run_id,
                snapshot,
                tick,
                consecutive_failures=consecutive_failures,
                model_cost_usd=model_cost_usd,
            )
            self._write_events(connection, run_id, events)

    def record(
        self,
        run_id: str,
        record: DecisionRecord,
        snapshot: AccountSnapshot,
        tick: MarketTick,
        *,
        consecutive_failures: int,
        model_cost_usd: Decimal,
        events: tuple[AccountEvent, ...],
    ) -> None:
        record_json = self._json(record)
        snapshot_json = self._json(snapshot)
        tick_json = self._json(tick)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, record.decision_id, record.timestamp_ms, record.status, str(record.model_cost_usd), record_json),
                )
                connection.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
                    (run_id, record.decision_id, record.timestamp_ms, snapshot_json, tick_json),
                )
                self._write_state(
                    connection,
                    run_id,
                    snapshot,
                    tick,
                    consecutive_failures=consecutive_failures,
                    model_cost_usd=model_cost_usd,
                )
                self._write_events(connection, run_id, events)
        except sqlite3.IntegrityError as error:
            raise StoreError(f"decision is already persisted: {record.decision_id}") from error
