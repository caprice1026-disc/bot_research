"""Small SQLite persistence for offline sequential runs and safe restart checks."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any

from trading_core.accounting.models import AccountSnapshot
from trading_core.market_data.models import MarketTick

if TYPE_CHECKING:
    from .runner import DecisionRecord


class StoreError(ValueError):
    """Raised when persisted state cannot safely be reused."""


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


class RunStore:
    """One local run database in WAL mode; it does not share any live trader DB."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
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
            connection.execute("PRAGMA user_version=1")

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

    def total_model_cost(self, run_id: str) -> Decimal:
        with self._connect() as connection:
            rows = connection.execute("SELECT model_cost_usd FROM decisions WHERE run_id = ?", (run_id,))
            return sum((Decimal(row[0]) for row in rows), Decimal("0"))

    def load_latest(self, run_id: str) -> tuple[AccountSnapshot, MarketTick] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT snapshot_json, tick_json FROM snapshots
                   WHERE run_id = ? ORDER BY timestamp_ms DESC, rowid DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return _snapshot_from_json(json.loads(row[0])), _tick_from_json(json.loads(row[1]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StoreError("persisted snapshot is invalid") from error

    def record(self, run_id: str, record: DecisionRecord, snapshot: AccountSnapshot, tick: MarketTick) -> None:
        record_json = json.dumps(_plain(asdict(record)), sort_keys=True, separators=(",", ":"))
        snapshot_json = json.dumps(_plain(asdict(snapshot)), sort_keys=True, separators=(",", ":"))
        tick_json = json.dumps(_plain(asdict(tick)), sort_keys=True, separators=(",", ":"))
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
        except sqlite3.IntegrityError as error:
            raise StoreError(f"decision is already persisted: {record.decision_id}") from error
