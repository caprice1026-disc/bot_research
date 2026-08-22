from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Append deterministic JSONL rows without rewriting prior chunks."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_parquet(path: Path, rows: list[dict[str, Any]], schema_version: str = "0.1") -> None:
    if not rows:
        raise ValueError("cannot write an empty parquet dataset")
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = sorted({key for row in rows for key in row})
    string_columns = {
        key
        for key in columns
        if any(isinstance(row.get(key), str) for row in rows if row.get(key) is not None)
        and any(not isinstance(row.get(key), str) for row in rows if row.get(key) is not None)
    }
    normalized = []
    for row in rows:
        normalized.append(
            {
                key: (None if row.get(key) is None else str(row[key])) if key in string_columns else row.get(key)
                for key in columns
            }
        )
    table = pa.Table.from_pylist(normalized)
    metadata = dict(table.schema.metadata or {})
    metadata[b"schema_version"] = schema_version.encode("ascii")
    table = table.replace_schema_metadata(metadata)
    pq.write_table(table, path, compression="zstd")


def read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()
