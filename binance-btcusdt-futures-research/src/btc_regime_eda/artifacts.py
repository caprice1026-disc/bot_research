from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pandas as pd


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{uuid4().hex}.tmp")


def write_csv_atomic(frame: pd.DataFrame, path: Path, *, index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        frame.to_csv(temporary, index=index)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(payload: object, path: Path, *, ensure_ascii: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=ensure_ascii, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_text_atomic(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_figure_atomic(figure: object, path: Path, **kwargs: object) -> None:
    """Save a matplotlib figure without exposing a partially written PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{uuid4().hex}.tmp{path.suffix}")
    try:
        figure.savefig(temporary, **kwargs)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
