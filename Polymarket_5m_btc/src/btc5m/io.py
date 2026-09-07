from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from btc5m.storage import read_jsonl


def load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        return [dict(row) for row in pl.read_parquet(path).to_dicts()]
    return read_jsonl(path)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
