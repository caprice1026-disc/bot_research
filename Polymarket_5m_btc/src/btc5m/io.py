from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from btc5m.storage import read_jsonl, read_sqlite_rows


def load_rows(path: Path, *, source: str | None = None) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        rows = [dict(row) for row in pl.read_parquet(path).to_dicts()]
    elif path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return read_sqlite_rows(path, source=source)
    else:
        rows = read_jsonl(path)
    return rows if source is None else [row for row in rows if row.get("source") == source]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
