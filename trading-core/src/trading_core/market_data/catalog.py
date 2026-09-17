"""Content-addressed references to normalized local market datasets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


class CatalogError(ValueError):
    """Raised when a dataset reference is not a complete, expected artifact."""


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class DatasetRef:
    path: Path
    venue: str
    symbol: str
    interval_ms: int
    start_ms: int
    end_ms: int
    sha256: str
    dataset_id: str


@dataclass(frozen=True)
class CoverageReport:
    status: str
    requested_start_ms: int
    requested_end_ms: int
    observed_start_ms: int | None
    observed_end_ms: int | None
    gaps: tuple[tuple[int, int], ...]
    funding_gaps: tuple[tuple[int, int], ...] = ()


def _read_catalog(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"cannot read dataset catalog: {path}") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("datasets"), list):
        raise CatalogError("catalog must be schema version 1 with a datasets list")
    return raw


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1_048_576), b""):
                digest.update(block)
    except OSError as error:
        raise CatalogError(f"cannot read dataset: {path}") from error
    return digest.hexdigest()


def resolve_dataset(catalog_path: Path, dataset_id: str, expected_sha256: str) -> DatasetRef:
    """Resolve a relative catalog entry only when its declared content hash matches."""

    if not _SHA256.fullmatch(expected_sha256):
        raise CatalogError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    entry = next(
        (candidate for candidate in _read_catalog(catalog_path)["datasets"] if isinstance(candidate, dict) and candidate.get("dataset_id") == dataset_id),
        None,
    )
    if entry is None:
        raise CatalogError(f"dataset is not registered: {dataset_id}")
    try:
        declared_hash = str(entry["sha256"])
        relative_path = Path(str(entry["path"]))
        root = catalog_path.parent.resolve()
        resolved = (root / relative_path).resolve()
        if not resolved.is_relative_to(root):
            raise CatalogError("dataset path escapes the catalog directory")
        reference = DatasetRef(
            path=resolved,
            venue=str(entry["venue"]),
            symbol=str(entry["symbol"]),
            interval_ms=int(entry["interval_ms"]),
            start_ms=int(entry["start_ms"]),
            end_ms=int(entry["end_ms"]),
            sha256=declared_hash,
            dataset_id=dataset_id,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CatalogError(f"dataset entry is invalid: {dataset_id}") from error
    if declared_hash != expected_sha256:
        raise CatalogError("catalog hash does not match the expected SHA-256")
    if not _SHA256.fullmatch(declared_hash):
        raise CatalogError("catalog SHA-256 must be 64 lowercase hexadecimal characters")
    if reference.interval_ms <= 0 or reference.start_ms < 0 or reference.end_ms <= reference.start_ms:
        raise CatalogError("dataset interval and range are invalid")
    return reference


def verify_dataset(reference: DatasetRef) -> CoverageReport:
    """Re-hash a JSONL candle artifact and report explicit missing time ranges."""

    if _sha256_path(reference.path) != reference.sha256:
        raise CatalogError("dataset content does not match its catalog SHA-256")
    rows: list[tuple[int, int]] = []
    try:
        with reference.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise TypeError("row is not an object")
                start, end = int(row["open_time_ms"]), int(row["close_exclusive_ms"])
                if end != start + reference.interval_ms:
                    raise CatalogError(f"dataset row {line_number} has a wrong interval")
                rows.append((start, end))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, CatalogError):
            raise
        raise CatalogError(f"dataset JSONL is invalid: {reference.path}") from error
    gaps: list[tuple[int, int]] = []
    for previous, current in zip(rows, rows[1:], strict=False):
        if current[0] != previous[1]:
            gaps.append((previous[1], current[0]))
    observed_start = rows[0][0] if rows else None
    observed_end = rows[-1][1] if rows else None
    complete = (
        observed_start == reference.start_ms
        and observed_end == reference.end_ms
        and not gaps
    )
    return CoverageReport(
        status="complete" if complete else "incomplete",
        requested_start_ms=reference.start_ms,
        requested_end_ms=reference.end_ms,
        observed_start_ms=observed_start,
        observed_end_ms=observed_end,
        gaps=tuple(gaps),
    )
