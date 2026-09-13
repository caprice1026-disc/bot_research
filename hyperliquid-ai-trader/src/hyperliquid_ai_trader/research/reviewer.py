"""Deterministic UTC review and strategy-patch safety checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..strategy import StrategyPatchError, apply_strategy_patch


class ReviewerError(ValueError):
    """Raised when review evidence or timing is invalid."""


REVIEW_PAUSE_MS = 5 * 60 * 1000
MAX_OPERATIONS = 2
MAX_RULES = 8
MAX_RULE_LENGTH = 240
MAX_HYPOTHESIS_LENGTH = 400
MAX_STRATEGY_BYTES = 16 * 1024
MAX_TOTAL_CALIBRATION_OFFSET = 0.2
MAX_DAILY_CALIBRATION_DELTA = 0.02
MIN_RULE_OBSERVATIONS = 20
MIN_CONFIDENCE_PREDICTIONS = 50


def patch_deadline_ms(boundary_ms: int) -> int:
    return boundary_ms + REVIEW_PAUSE_MS


def patch_is_on_time(*, completed_at_ms: int, boundary_ms: int) -> bool:
    """00:05:00 is held; only a patch completed before the deadline applies."""

    return completed_at_ms < patch_deadline_ms(boundary_ms)


def _evidence_ids(operation: Mapping[str, Any]) -> list[str]:
    evidence = operation.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ReviewerError("operation evidence is required")
    ids = evidence.get("trade_ids", evidence.get("evidence_ids"))
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) and item for item in ids):
        raise ReviewerError("operation evidence IDs are required")
    return ids


def validate_evidence_ids(
    records: Iterable[Mapping[str, Any]],
    evidence_ids: Iterable[str],
    *,
    experiment_id: str | None = None,
    cutoff_ms: int | None = None,
) -> None:
    """Ensure every cited record is closed and available at the UTC cutoff."""

    wanted = list(evidence_ids)
    if len(set(wanted)) != len(wanted):
        raise ReviewerError("review evidence IDs must be unique")
    by_id = {str(row.get("episode_id", row.get("evidence_id"))): row for row in records}
    for evidence_id in wanted:
        row = by_id.get(evidence_id)
        if row is None or row.get("status", "closed") != "closed":
            raise ReviewerError("review evidence must be closed")
        if experiment_id is not None and row.get("experiment_id", experiment_id) != experiment_id:
            raise ReviewerError("review evidence belongs to another experiment")
        closed_at = row.get("closed_at_ms")
        if cutoff_ms is not None and (not isinstance(closed_at, int) or closed_at > cutoff_ms):
            raise ReviewerError("review evidence is after the cutoff")


def validate_patch_limits(
    state: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    evidence_records: Iterable[Mapping[str, Any]] = (),
    experiment_id: str | None = None,
    cutoff_ms: int | None = None,
    previous_calibration: Mapping[str, float] | None = None,
) -> None:
    operations = patch.get("operations")
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise ReviewerError("review patch has too many operations")
    records = list(evidence_records)
    all_ids: list[str] = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ReviewerError("review operation must be an object")
        ids = _evidence_ids(operation)
        all_ids.extend(ids)
        validate_evidence_ids(records, ids, experiment_id=experiment_id, cutoff_ms=cutoff_ms)
        path = operation.get("path")
        value = operation.get("value")
        if path in {"/active_rules", "/failure_modes"} and isinstance(value, str) and len(value.strip()) > MAX_RULE_LENGTH:
            raise ReviewerError("strategy rule is too long")
        if path == "/market_hypothesis" and isinstance(value, str) and len(value.strip()) > MAX_HYPOTHESIS_LENGTH:
            raise ReviewerError("market hypothesis is too long")
    if len(all_ids) != len(set(all_ids)):
        raise ReviewerError("review evidence IDs must be unique")
    candidate = dict(state)
    candidate_rules = list(candidate.get("active_rules", []))
    candidate_failures = list(candidate.get("failure_modes", []))
    for operation in operations:
        path, op, value = operation.get("path"), operation.get("op"), operation.get("value")
        target = candidate_rules if path == "/active_rules" else candidate_failures if path == "/failure_modes" else None
        if target is not None and op == "add" and isinstance(value, str) and value.strip() not in target:
            target.append(value.strip())
        if target is not None and op == "remove" and isinstance(value, str) and value.strip() in target:
            target.remove(value.strip())
    if len(candidate_rules) > MAX_RULES or len(candidate_failures) > MAX_RULES:
        raise ReviewerError("strategy has too many rules")
    import json
    if len(json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode("utf-8")) > MAX_STRATEGY_BYTES:
        raise ReviewerError("strategy exceeds size limit")
    if previous_calibration is not None:
        calibration = candidate.get("confidence_calibration", {})
        total = sum(abs(float(calibration.get(side, 0))) for side in ("long", "short"))
        previous_total = sum(abs(float(previous_calibration.get(side, 0))) for side in ("long", "short"))
        if total > MAX_TOTAL_CALIBRATION_OFFSET or total - previous_total > MAX_DAILY_CALIBRATION_DELTA:
            raise ReviewerError("confidence calibration delta is too large")


def adjust_confidence(raw: float, *, side: str, strategy: Mapping[str, Any]) -> float:
    """Apply Reviewer calibration exactly once, outside the Trader prompt."""

    offset = float(strategy.get("confidence_calibration", {}).get(side.lower(), 0.0))
    return max(0.0, min(1.0, float(raw) + offset))


@dataclass(frozen=True)
class ReviewResult:
    status: str
    strategy: dict[str, Any]
    reason: str | None = None


def review_at_boundary(
    *,
    state: dict[str, Any],
    patch: Mapping[str, Any] | None,
    review_cycle: int,
    boundary_ms: int,
    completed_at_ms: int | None,
    evidence_records: Iterable[Mapping[str, Any]] = (),
) -> ReviewResult:
    if patch is None:
        return ReviewResult("validated_no_change", dict(state))
    if completed_at_ms is None or not patch_is_on_time(completed_at_ms=completed_at_ms, boundary_ms=boundary_ms):
        return ReviewResult("held_late_patch", dict(state), "patch completed at or after the five-minute pause")
    try:
        validate_patch_limits(state, patch, evidence_records=evidence_records, cutoff_ms=boundary_ms)
        updated = apply_strategy_patch(state, dict(patch), review_cycle=review_cycle)
    except (ReviewerError, StrategyPatchError) as error:
        return ReviewResult("failed", dict(state), str(error))
    return ReviewResult("validated_patch", updated)
