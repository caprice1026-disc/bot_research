"""Evidence selection facade used by the research Reviewer."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .reviewer import ReviewerError, validate_evidence_ids


def eligible_evidence(
    records: Iterable[Mapping[str, Any]], *, experiment_id: str, cutoff_ms: int
) -> list[dict[str, Any]]:
    """Return only closed records from this experiment before the UTC cutoff."""

    result = []
    for record in records:
        if record.get("experiment_id", experiment_id) != experiment_id:
            continue
        if record.get("status", "closed") != "closed":
            continue
        closed_at = record.get("closed_at_ms")
        if isinstance(closed_at, int) and closed_at <= cutoff_ms:
            result.append(dict(record))
    return result


__all__ = ["ReviewerError", "eligible_evidence", "validate_evidence_ids"]
