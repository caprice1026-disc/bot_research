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
            CREATE TABLE IF NOT EXISTS decisions (
                run_id TEXT NOT NULL,
                slot INTEGER NOT NULL,
                arguments_json TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                temperature REAL NOT NULL,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY (run_id, slot),
                FOREIGN KEY (run_id, slot) REFERENCES cycles(run_id, slot)
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
            CREATE TABLE IF NOT EXISTS funding_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                funding_id TEXT NOT NULL UNIQUE,
                amount TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS episode_metrics (
                run_id TEXT NOT NULL,
                slot INTEGER NOT NULL,
                mfe_pct TEXT NOT NULL,
                mae_pct TEXT NOT NULL,
                method TEXT NOT NULL,
                PRIMARY KEY (run_id, slot),
                FOREIGN KEY (run_id, slot) REFERENCES cycles(run_id, slot)
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

    def record_decision(
        self,
        *,
        run_id: str,
        slot: int,
        arguments: dict[str, Any],
        prompt_hash: str,
        model: str,
        temperature: float,
        created_at_ms: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO decisions
                (run_id, slot, arguments_json, prompt_hash, model, temperature, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, slot) DO NOTHING
            """,
            (
                run_id,
                slot,
                _json(arguments),
                prompt_hash,
                model,
                temperature,
                created_at_ms,
            ),
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

    def record_fill(
        self,
        *,
        run_id: str,
        slot: int,
        fill_id: str,
        side: str,
        size: Decimal,
        price: Decimal,
        fee: Decimal,
        closed_pnl: Decimal,
        timestamp_ms: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO fills
                (run_id, slot, fill_id, side, size, price, fee, closed_pnl, timestamp_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                slot,
                fill_id,
                side,
                str(size),
                str(price),
                str(fee),
                str(closed_pnl),
                timestamp_ms,
            ),
        )
        self.connection.commit()

    def record_funding(
        self,
        *,
        run_id: str,
        funding_id: str,
        amount: Decimal,
        timestamp_ms: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO funding_payments
                (run_id, funding_id, amount, timestamp_ms)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, funding_id, str(amount), timestamp_ms),
        )
        self.connection.commit()

    def record_equity(
        self,
        *,
        run_id: str,
        timestamp_ms: int,
        equity: Decimal,
        withdrawable: Decimal,
        mark: Decimal,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO equity_snapshots
                (run_id, timestamp_ms, equity, withdrawable, mark)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, timestamp_ms, str(equity), str(withdrawable), str(mark)),
        )
        self.connection.commit()

    def record_episode_metric(
        self,
        *,
        run_id: str,
        slot: int,
        mfe_pct: Decimal,
        mae_pct: Decimal,
        method: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO episode_metrics (run_id, slot, mfe_pct, mae_pct, method)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, slot) DO NOTHING
            """,
            (run_id, slot, str(mfe_pct), str(mae_pct), method),
        )
        self.connection.commit()

    def finish_run(
        self,
        *,
        run_id: str,
        completed_at_ms: int,
        final_equity: Decimal,
        final_mark: Decimal,
        status: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE runs
            SET completed_at_ms=?, final_equity=?, final_mark=?, status=?
            WHERE run_id=?
            """,
            (completed_at_ms, str(final_equity), str(final_mark), status, run_id),
        )
        self.connection.commit()

    def known_cloids(self, run_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT cloid FROM orders WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return {str(row["cloid"]) for row in rows}

    def slot_for_oid(self, run_id: str, oid: int) -> int | None:
        row = self.connection.execute(
            "SELECT slot FROM orders WHERE run_id=? AND oid=? ORDER BY id LIMIT 1",
            (run_id, oid),
        ).fetchone()
        return int(row["slot"]) if row else None

    def slot_for_unmapped_fill(
        self,
        run_id: str,
        *,
        timestamp_ms: int,
        side: str | None = None,
    ) -> int | None:
        """Find the open episode whose matching entry precedes an unmapped close."""
        if side in {"long", "short"}:
            row = self.connection.execute(
                """
                SELECT entry.slot
                FROM fills AS entry
                JOIN cycles AS c
                  ON c.run_id=entry.run_id AND c.slot=entry.slot
                WHERE entry.run_id=?
                  AND entry.side=?
                  AND entry.timestamp_ms<=?
                  AND CAST(entry.closed_pnl AS REAL)=0
                  AND c.status IN ('ordered', 'simulated')
                  AND NOT EXISTS (
                      SELECT 1 FROM fills AS close_fill
                      WHERE close_fill.run_id=entry.run_id
                        AND close_fill.slot=entry.slot
                        AND CAST(close_fill.closed_pnl AS REAL) != 0
                  )
                ORDER BY entry.timestamp_ms DESC, entry.id DESC
                LIMIT 1
                """,
                (run_id, side, timestamp_ms),
            ).fetchone()
            if row is not None:
                return int(row["slot"])

        # Fallback for an entry that is not available in the current API window.
        row = self.connection.execute(
            """
            SELECT c.slot
            FROM cycles AS c
            WHERE c.run_id=?
              AND c.scheduled_at_ms<=?
              AND c.status IN ('ordered', 'simulated')
              AND NOT EXISTS (
                  SELECT 1 FROM fills AS f
                  WHERE f.run_id=c.run_id
                    AND f.slot=c.slot
                    AND CAST(f.closed_pnl AS REAL) != 0
              )
            ORDER BY c.scheduled_at_ms DESC
            LIMIT 1
            """,
            (run_id, timestamp_ms),
        ).fetchone()
        return int(row["slot"]) if row else None

    def realized_net_pnl(self, run_id: str) -> Decimal:
        fills = self.connection.execute(
            "SELECT closed_pnl, fee FROM fills WHERE run_id=?",
            (run_id,),
        ).fetchall()
        funding = self.connection.execute(
            "SELECT amount FROM funding_payments WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return (
            sum((Decimal(str(row["closed_pnl"])) - Decimal(str(row["fee"])) for row in fills), Decimal("0"))
            + sum((Decimal(str(row["amount"])) for row in funding), Decimal("0"))
        )

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

    def recent_closed_trades(
        self,
        run_id: str,
        *,
        limit: int,
        include_context: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, slot, side, size, price, fee, closed_pnl, timestamp_ms
            FROM fills
            WHERE run_id=?
            ORDER BY timestamp_ms ASC, id ASC
            """,
            (run_id,),
        ).fetchall()
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            slot = int(row["slot"])
            item = grouped.setdefault(
                slot,
                {
                    "id": int(row["id"]),
                    "slot": slot,
                    "side": str(row["side"]),
                    "size": str(row["size"]),
                    "price": str(row["price"]),
                    "fee": Decimal("0"),
                    "gross_pnl": Decimal("0"),
                    "timestamp_ms": int(row["timestamp_ms"]),
                },
            )
            item["fee"] += Decimal(str(row["fee"]))
            item["gross_pnl"] += Decimal(str(row["closed_pnl"]))
            if Decimal(str(row["closed_pnl"])) != 0:
                item["id"] = int(row["id"])
                item["side"] = str(row["side"])
                item["size"] = str(row["size"])
                item["price"] = str(row["price"])
                item["timestamp_ms"] = int(row["timestamp_ms"])

        closed: list[dict[str, Any]] = []
        for item in grouped.values():
            gross = item["gross_pnl"]
            if gross == 0:
                continue
            fee = item["fee"]
            closed.append(
                {
                    "id": item["id"],
                    "slot": item["slot"],
                    "side": item["side"],
                    "size": item["size"],
                    "price": item["price"],
                    "fee": str(fee),
                    "gross_pnl": str(gross),
                    # Keep this legacy key for report/backward compatibility.
                    "closed_pnl": str(gross),
                    "net_pnl": str(gross - fee),
                    "timestamp_ms": item["timestamp_ms"],
                }
            )
        closed.sort(key=lambda item: (int(item["timestamp_ms"]), int(item["id"])))
        closed = closed[-max(0, limit) :]
        if include_context:
            for item in closed:
                cycle = self.connection.execute(
                    """
                    SELECT decision_json, features_json FROM cycles
                    WHERE run_id=? AND slot=?
                    """,
                    (run_id, item["slot"]),
                ).fetchone()
                if cycle is not None:
                    item["decision"] = json.loads(cycle["decision_json"]) if cycle["decision_json"] else {}
                    item["features"] = json.loads(cycle["features_json"]) if cycle["features_json"] else {}
        return closed

    def new_closed_trades_since_review(
        self,
        run_id: str,
        closed_trades: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Filter closed trades included in any prior Reviewer input.

        A review with no new trades used to replace the latest input with the
        cumulative sample.  Looking only at that latest row made older trades
        appear new again on the following review.  The union across all prior
        inputs makes the boundary restart-safe and also understands the
        legacy ``closed_trades`` key.
        """
        current_ids = {str(trade["id"]) for trade in closed_trades if trade.get("id") is not None}
        if not current_ids:
            return []
        row = self.connection.execute(
            """
            SELECT input_json FROM reviews
            WHERE run_id=? ORDER BY review_index ASC
            """,
            (run_id,),
        ).fetchall()
        if not row:
            return list(closed_trades)
        previous_ids: set[str] = set()
        for review in row:
            try:
                payload = json.loads(review["input_json"])
                samples = list(payload.get("new_closed_trades", []))
                samples.extend(payload.get("closed_trades", []))
                samples.extend(payload.get("cumulative_closed_trades", []))
                previous_ids.update(
                    str(trade["id"])
                    for trade in samples
                    if isinstance(trade, dict) and trade.get("id") is not None
                )
            except (TypeError, ValueError, AttributeError, KeyError):
                continue
        return [trade for trade in closed_trades if str(trade.get("id")) not in previous_ids]

    def abstention_reference_outcomes(
        self,
        run_id: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Build mark-to-mark, fee-adjusted references for skipped cycles.

        These are explicitly counterfactual: they use the next completed
        five-minute observation and an estimated round-trip cost, not fills.
        Keeping them derived from cycles avoids a second mutable source of
        truth while still making the result restart-safe.
        """
        rows = self.connection.execute(
            """
            SELECT skipped.slot,
                   skipped.features_json AS skipped_features_json,
                   skipped.decision_json AS skipped_decision_json,
                   next_cycle.features_json AS next_features_json
            FROM cycles AS skipped
            JOIN cycles AS next_cycle
              ON next_cycle.run_id=skipped.run_id
             AND next_cycle.slot=skipped.slot + 1
            WHERE skipped.run_id=?
              AND skipped.status='abstained'
              AND skipped.features_json IS NOT NULL
              AND skipped.decision_json IS NOT NULL
              AND next_cycle.features_json IS NOT NULL
            ORDER BY skipped.slot DESC
            LIMIT ?
            """,
            (run_id, max(0, limit)),
        ).fetchall()
        outcomes: list[dict[str, Any]] = []
        for row in rows:
            try:
                features = json.loads(row["skipped_features_json"])
                decision = json.loads(row["skipped_decision_json"])
                next_features = json.loads(row["next_features_json"])
                entry = Decimal(str(features.get("mark", features.get("mid", "0"))))
                exit_price = Decimal(str(next_features.get("mark", next_features.get("mid", "0"))))
                if entry <= 0 or exit_price <= 0:
                    continue
                side = str(decision.get("side", ""))
                if side not in {"long", "short"}:
                    continue
                direction = Decimal("1") if side == "long" else Decimal("-1")
                gross_bps = (exit_price / entry - Decimal("1")) * direction * Decimal("10000")
                costs = features.get("costs", {})
                cost_bps = Decimal(str(costs.get("estimated_round_trip_cost_bps", "0")))
                outcomes.append(
                    {
                        "slot": int(row["slot"]),
                        "side": side,
                        "entry_mark": str(entry),
                        "next_mark": str(exit_price),
                        "gross_return_bps": str(gross_bps.quantize(Decimal("0.000001"))),
                        "estimated_round_trip_cost_bps": str(cost_bps),
                        "net_return_bps": str((gross_bps - cost_bps).quantize(Decimal("0.000001"))),
                        "decision": decision,
                        "method": "next_cycle_mark_minus_estimated_cost",
                        "counterfactual": True,
                    }
                )
            except (TypeError, ValueError, ArithmeticError, KeyError):
                continue
        outcomes.sort(key=lambda item: int(item["slot"]))
        return outcomes

    def new_abstention_reference_outcomes_since_review(
        self,
        run_id: str,
        outcomes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return counterfactual outcomes not passed to an earlier review."""
        current_slots = {str(item["slot"]) for item in outcomes if item.get("slot") is not None}
        if not current_slots:
            return []
        rows = self.connection.execute(
            "SELECT input_json FROM reviews WHERE run_id=? ORDER BY review_index ASC",
            (run_id,),
        ).fetchall()
        previous_slots: set[str] = set()
        for row in rows:
            try:
                payload = json.loads(row["input_json"])
                references = payload.get("abstention_reference_outcomes", [])
                references = list(references) + list(payload.get("new_abstention_reference_outcomes", []))
                previous_slots.update(
                    str(item["slot"])
                    for item in references
                    if isinstance(item, dict) and item.get("slot") is not None
                )
            except (TypeError, ValueError, AttributeError, KeyError):
                continue
        return [item for item in outcomes if str(item.get("slot")) not in previous_slots]

    def has_new_closed_trades(self, run_id: str, closed_trades: list[dict[str, Any]]) -> bool:
        """Return whether any closed trade was absent from prior review inputs."""
        return bool(self.new_closed_trades_since_review(run_id, closed_trades))

    def record_review(
        self,
        *,
        run_id: str,
        review_index: int,
        created_at_ms: int,
        model: str,
        status: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any] | None,
        error_type: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO reviews
                (run_id, review_index, created_at_ms, model, status, input_json, output_json, error_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, review_index) DO NOTHING
            """,
            (
                run_id,
                review_index,
                created_at_ms,
                model,
                status,
                _json(input_payload),
                _json(output_payload) if output_payload is not None else None,
                error_type,
            ),
        )
        self.connection.commit()

    def record_patch(
        self,
        *,
        run_id: str,
        base_version: int,
        next_version: int | None,
        patch: dict[str, Any],
        accepted: bool,
        reason: str,
        created_at_ms: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO patches
                (run_id, base_version, next_version, patch_json, accepted, reason, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                base_version,
                next_version,
                _json(patch),
                int(accepted),
                reason,
                created_at_ms,
            ),
        )
        self.connection.commit()

    def record_event(
        self,
        *,
        run_id: str,
        timestamp_ms: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        payload_json = _json(payload)
        self.connection.execute(
            """
            INSERT INTO events (run_id, timestamp_ms, event_type, payload_json)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM events
                WHERE run_id=? AND event_type=? AND payload_json=?
            )
            """,
            (run_id, timestamp_ms, event_type, payload_json, run_id, event_type, payload_json),
        )
        self.connection.commit()

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
