"""Incremental SQLite evidence store for local and job-style runs."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER,
                initial_equity TEXT NOT NULL,
                final_equity TEXT,
                initial_mark TEXT NOT NULL,
                final_mark TEXT,
                git_sha TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running'
            );
            CREATE TABLE IF NOT EXISTS cycles (
                run_id TEXT NOT NULL,
                slot INTEGER NOT NULL,
                scheduled_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER,
                strategy_version INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                features_json TEXT,
                decision_json TEXT,
                prompt_hash TEXT,
                model TEXT,
                error_type TEXT,
                PRIMARY KEY (run_id, slot),
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                slot INTEGER NOT NULL,
                leg TEXT NOT NULL,
                cloid TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                size TEXT NOT NULL,
                price TEXT NOT NULL,
                oid INTEGER,
                created_at_ms INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (run_id, slot) REFERENCES cycles(run_id, slot)
            );
            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                slot INTEGER NOT NULL,
                fill_id TEXT NOT NULL UNIQUE,
                side TEXT NOT NULL,
                size TEXT NOT NULL,
                price TEXT NOT NULL,
                fee TEXT NOT NULL,
                closed_pnl TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                equity TEXT NOT NULL,
                withdrawable TEXT NOT NULL,
                mark TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                review_index INTEGER NOT NULL,
                created_at_ms INTEGER NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL,
                output_json TEXT,
                error_type TEXT,
                UNIQUE(run_id, review_index)
            );
            CREATE TABLE IF NOT EXISTS strategy_versions (
                run_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                parent_version INTEGER,
                state_json TEXT NOT NULL,
                patch_json TEXT,
                model TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (run_id, version)
            );
            CREATE TABLE IF NOT EXISTS patches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                base_version INTEGER NOT NULL,
                next_version INTEGER,
                patch_json TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def create_run(
        self,
        *,
        run_id: str,
        mode: str,
        started_at_ms: int,
        initial_equity: Decimal,
        initial_mark: Decimal,
        git_sha: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO runs
                (run_id, mode, started_at_ms, initial_equity, initial_mark, git_sha)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, mode, started_at_ms, str(initial_equity), str(initial_mark), git_sha),
        )
        self.connection.commit()

    def reserve_cycle(
        self,
        run_id: str,
        slot: int,
        *,
        scheduled_at_ms: int,
        strategy_version: int,
    ) -> bool:
        try:
            self.connection.execute(
                """
                INSERT INTO cycles
                    (run_id, slot, scheduled_at_ms, strategy_version)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, slot, scheduled_at_ms, strategy_version),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def complete_cycle(
        self,
        *,
        run_id: str,
        slot: int,
        status: str,
        features: dict[str, Any],
        decision: dict[str, Any] | None,
        prompt_hash: str | None,
        model: str | None,
        error_type: str | None,
        completed_at_ms: int = 0,
    ) -> None:
        self.connection.execute(
            """
            UPDATE cycles
            SET completed_at_ms=?, status=?, features_json=?, decision_json=?,
                prompt_hash=?, model=?, error_type=?
            WHERE run_id=? AND slot=?
            """,
            (
                completed_at_ms,
                status,
                _json(features),
                _json(decision) if decision is not None else None,
                prompt_hash,
                model,
                error_type,
                run_id,
                slot,
            ),
        )
        self.connection.commit()

    def record_order(
        self,
        *,
        run_id: str,
        slot: int,
        leg: str,
        cloid: str,
        status: str,
        size: Decimal,
        price: Decimal,
        oid: int | None = None,
        created_at_ms: int = 0,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO orders
                (run_id, slot, leg, cloid, status, size, price, oid, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cloid) DO UPDATE SET status=excluded.status, oid=excluded.oid
            """,
            (run_id, slot, leg, cloid, status, str(size), str(price), oid, created_at_ms),
        )
        self.connection.commit()

    def known_cloids(self, run_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT cloid FROM orders WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return {str(row["cloid"]) for row in rows}

    def save_strategy_version(
        self,
        *,
        run_id: str,
        version: int,
        parent_version: int | None,
        state: dict[str, Any],
        patch: dict[str, Any] | None,
        model: str,
        created_at_ms: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO strategy_versions
                (run_id, version, parent_version, state_json, patch_json, model, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                version,
                parent_version,
                _json(state),
                _json(patch) if patch is not None else None,
                model,
                created_at_ms,
            ),
        )
        self.connection.commit()

    def load_latest_strategy(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT state_json FROM strategy_versions
            WHERE run_id=? ORDER BY version DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def get_cycle(self, run_id: str, slot: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM cycles WHERE run_id=? AND slot=?",
            (run_id, slot),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["features"] = json.loads(result.pop("features_json")) if result["features_json"] else None
        result["decision"] = json.loads(result.pop("decision_json")) if result["decision_json"] else None
        return result

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
