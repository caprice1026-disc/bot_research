from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

CANONICAL_COLUMNS = (
    "source",
    "symbol",
    "market_id",
    "event_type",
    "source_event_ts",
    "source_publish_ts",
    "local_receive_ts",
    "local_monotonic_ns",
    "sequence_id",
    "price",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "receive_id",
    "connection_id",
    "raw_payload",
)

_EMPTY_SCHEMA = {
    "source": pl.String,
    "symbol": pl.String,
    "market_id": pl.String,
    "event_type": pl.String,
    "source_event_ts": pl.Int64,
    "source_publish_ts": pl.Int64,
    "local_receive_ts": pl.String,
    "local_monotonic_ns": pl.Int64,
    "sequence_id": pl.String,
    "price": pl.String,
    "bid": pl.String,
    "ask": pl.String,
    "bid_size": pl.String,
    "ask_size": pl.String,
    "receive_id": pl.String,
    "connection_id": pl.String,
    "raw_payload": pl.String,
}

_ARROW_SCHEMA = pa.schema(
    [
        pa.field("source", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("market_id", pa.string()),
        pa.field("event_type", pa.string()),
        pa.field("source_event_ts", pa.int64()),
        pa.field("source_publish_ts", pa.int64()),
        pa.field("local_receive_ts", pa.string()),
        pa.field("local_monotonic_ns", pa.int64()),
        pa.field("sequence_id", pa.string()),
        pa.field("price", pa.string()),
        pa.field("bid", pa.string()),
        pa.field("ask", pa.string()),
        pa.field("bid_size", pa.string()),
        pa.field("ask_size", pa.string()),
        pa.field("receive_id", pa.string()),
        pa.field("connection_id", pa.string()),
        pa.field("raw_payload", pa.string()),
    ]
)


def _safe_source(source: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in source
    )
    if not safe:
        raise ValueError("source must contain at least one path-safe character")
    return safe


def partition_path(root: Path, source: str, received: datetime) -> Path:
    normalized = (
        received
        if received.tzinfo is not None
        else received.replace(tzinfo=timezone.utc)
    )
    normalized = normalized.astimezone(timezone.utc)
    return (
        root
        / _safe_source(source)
        / f"date={normalized:%Y-%m-%d}"
        / f"hour={normalized:%H}"
        / "events.jsonl"
    )


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    append_jsonl_rows(path, (row,))


def append_jsonl_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
            )
            handle.write("\n")


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


class SQLiteEventStore:
    """Batch canonical events into one local SQLite database.

    Collection has one logical writer, even though source queues are separate.
    The lock keeps the connection safe when batches are flushed through
    ``asyncio.to_thread``.  WAL permits analysis readers while collection is
    appending; FULL synchronization keeps committed rows durable.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            str(path),
            check_same_thread=False,
            timeout=5.0,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                symbol TEXT,
                market_id TEXT,
                event_type TEXT NOT NULL,
                source_event_ts INTEGER,
                source_publish_ts INTEGER,
                local_receive_ts TEXT,
                local_monotonic_ns INTEGER,
                sequence_id TEXT,
                price TEXT,
                bid TEXT,
                ask TEXT,
                bid_size TEXT,
                ask_size TEXT,
                receive_id TEXT,
                connection_id TEXT,
                raw_payload TEXT
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_receive "
            "ON events(local_receive_ts, event_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_market_receive "
            "ON events(market_id, symbol, local_receive_ts, event_id)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_source_sequence "
            "ON events(source, sequence_id)"
        )
        self._connection.commit()

    @staticmethod
    def _value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bytes)):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def append_rows(self, rows: Iterable[Mapping[str, Any]]) -> int:
        values = [
            tuple(self._value(row.get(column)) for column in CANONICAL_COLUMNS)
            for row in rows
        ]
        if not values:
            return 0
        placeholders = ", ".join("?" for _ in CANONICAL_COLUMNS)
        columns = ", ".join(CANONICAL_COLUMNS)
        with self._lock:
            try:
                self._connection.executemany(
                    f"INSERT INTO events ({columns}) VALUES ({placeholders})",
                    values,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return len(values)

    def close(self) -> None:
        with self._lock:
            self._connection.commit()
            self._connection.close()

    def __enter__(self) -> "SQLiteEventStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def iter_sqlite_rows(
    path: Path,
    *,
    source: str | None = None,
) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        columns = ", ".join(CANONICAL_COLUMNS)
        if source is None:
            cursor = connection.execute(
                f"SELECT {columns} FROM events ORDER BY event_id"
            )
        else:
            cursor = connection.execute(
                f"SELECT {columns} FROM events WHERE source = ? ORDER BY event_id",
                (source,),
            )
        for row in cursor:
            yield dict(row)
    finally:
        connection.close()


def read_sqlite_rows(path: Path, *, source: str | None = None) -> list[dict[str, Any]]:
    return list(iter_sqlite_rows(path, source=source))


def _write_rows_to_parquet(
    rows: list[dict[str, Any]],
    output_path: Path,
) -> int:
    if rows:
        normalized = [
            {column: row.get(column) for column in CANONICAL_COLUMNS} for row in rows
        ]
        frame = pl.DataFrame(normalized)
    else:
        frame = pl.DataFrame(schema=_EMPTY_SCHEMA)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(output_path.name + ".part")
    if temporary_path.exists():
        temporary_path.unlink()
    frame.write_parquet(temporary_path, compression="zstd")
    temporary_path.replace(output_path)
    return len(rows)


def compact_jsonl_to_parquet(input_path: Path, output_path: Path) -> int:
    return _write_rows_to_parquet(read_jsonl(input_path), output_path)


def compact_sqlite_to_parquet(
    input_path: Path,
    output_path: Path,
    *,
    source: str | None = None,
    batch_size: int = 10_000,
) -> int:
    """Compact SQLite in bounded batches, without materializing the full table."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(output_path.name + ".part")
    if temporary_path.exists():
        temporary_path.unlink()
    writer: pq.ParquetWriter | None = None
    row_count = 0
    connection = sqlite3.connect(str(input_path))
    connection.row_factory = sqlite3.Row
    try:
        columns = ", ".join(CANONICAL_COLUMNS)
        if source is None:
            cursor = connection.execute(f"SELECT {columns} FROM events ORDER BY event_id")
        else:
            cursor = connection.execute(
                f"SELECT {columns} FROM events WHERE source = ? ORDER BY event_id",
                (source,),
            )
        writer = pq.ParquetWriter(temporary_path, _ARROW_SCHEMA, compression="zstd")
        while batch := cursor.fetchmany(batch_size):
            rows = [dict(row) for row in batch]
            writer.write_table(pa.Table.from_pylist(rows, schema=_ARROW_SCHEMA))
            row_count += len(rows)
        if row_count == 0:
            writer.write_table(pa.Table.from_pylist([], schema=_ARROW_SCHEMA))
    except Exception:
        if writer is not None:
            writer.close()
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    finally:
        connection.close()
    assert writer is not None
    writer.close()
    temporary_path.replace(output_path)
    return row_count
