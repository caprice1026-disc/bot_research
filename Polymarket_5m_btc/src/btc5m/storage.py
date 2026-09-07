from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import polars as pl

CANONICAL_COLUMNS = (
    "source",
    "symbol",
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
    "raw_payload",
)

_EMPTY_SCHEMA = {
    "source": pl.String,
    "symbol": pl.String,
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
    "raw_payload": pl.String,
}


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
        )
        handle.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def compact_jsonl_to_parquet(input_path: Path, output_path: Path) -> int:
    rows = read_jsonl(input_path)
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
