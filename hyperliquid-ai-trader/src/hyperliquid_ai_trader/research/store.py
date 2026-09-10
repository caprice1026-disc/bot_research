"""研究結果専用SQLite台帳。ライブ取引DBとは共有しない。"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


SCHEMA_VERSION = 1


class ResearchStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ResearchStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection:
            yield self.connection

    def _migrate(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, created_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('trade','shadow')),
                    status TEXT NOT NULL, entry_quantity TEXT NOT NULL, exit_quantity TEXT NOT NULL,
                    gross TEXT, fee TEXT, funding TEXT, net TEXT, quality TEXT NOT NULL,
                    closed_at_ms INTEGER, payload_json TEXT NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id),
                    UNIQUE(experiment_id, decision_id, kind)
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    status TEXT NOT NULL, cutoff_ms INTEGER NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS review_evidence (
                    review_id TEXT NOT NULL, evidence_id TEXT NOT NULL, evaluated INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(review_id, evidence_id),
                    FOREIGN KEY(review_id) REFERENCES reviews(review_id)
                );
                """
            )
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def create_experiment(self, experiment_id: str, manifest: dict[str, Any], created_at_ms: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO experiments VALUES (?, ?, ?)",
                (experiment_id, json.dumps(manifest, sort_keys=True), created_at_ms),
            )

    def record_episode(self, *, episode_id: str, experiment_id: str, decision_id: str,
                       kind: str, entry_quantity: str, exit_quantity: str,
                       gross: str | None, fee: str | None, funding: str | None,
                       net: str | None, quality: str, closed_at_ms: int | None,
                       payload: dict[str, Any]) -> None:
        status = "closed" if entry_quantity == exit_quantity and closed_at_ms is not None else "open"
        with self.connection:
            self.connection.execute(
                "INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (episode_id, experiment_id, decision_id, kind, status, entry_quantity,
                 exit_quantity, gross, fee, funding, net, quality, closed_at_ms,
                 json.dumps(payload, sort_keys=True)),
            )

    def confirmed_evidence(self, experiment_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT episode_id, kind, gross, fee, funding, net, quality, closed_at_ms
               FROM episodes WHERE experiment_id=? AND status='closed'
               ORDER BY closed_at_ms, episode_id""",
            (experiment_id,),
        )
        return [dict(row) for row in rows]

    def finish_review(self, *, review_id: str, experiment_id: str, cutoff_ms: int,
                      status: str, evidence_ids: list[str]) -> None:
        evaluated = status in {"validated_no_change", "validated_patch"}
        with self.connection:
            self.connection.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?)",
                (review_id, experiment_id, status, cutoff_ms),
            )
            self.connection.executemany(
                "INSERT INTO review_evidence VALUES (?, ?, ?)",
                [(review_id, evidence_id, int(evaluated)) for evidence_id in evidence_ids],
            )

    def unevaluated_evidence_ids(self, experiment_id: str) -> list[str]:
        rows = self.connection.execute(
            """SELECT e.episode_id FROM episodes e
               WHERE e.experiment_id=? AND e.status='closed' AND NOT EXISTS (
                 SELECT 1 FROM review_evidence re JOIN reviews r ON r.review_id=re.review_id
                 WHERE re.evidence_id=e.episode_id AND re.evaluated=1 AND r.experiment_id=e.experiment_id
               ) ORDER BY e.closed_at_ms, e.episode_id""",
            (experiment_id,),
        )
        return [row[0] for row in rows]
