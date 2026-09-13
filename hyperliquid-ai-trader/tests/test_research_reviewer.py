from hyperliquid_ai_trader.research.reviewer import (
    REVIEW_PAUSE_MS,
    ReviewerError,
    adjust_confidence,
    patch_is_on_time,
    review_at_boundary,
    validate_evidence_ids,
)
from hyperliquid_ai_trader.strategy import initial_strategy


def test_review_pause_holds_exact_five_minute_boundary() -> None:
    assert patch_is_on_time(completed_at_ms=REVIEW_PAUSE_MS - 1, boundary_ms=0)
    assert not patch_is_on_time(completed_at_ms=REVIEW_PAUSE_MS, boundary_ms=0)


def test_failed_review_does_not_consume_or_change_strategy() -> None:
    state = initial_strategy()
    result = review_at_boundary(
        state=state,
        patch={"base_version": 1, "operations": [{"op": "add", "path": "/active_rules", "value": "x", "evidence": {"trade_ids": ["missing"]}}]},
        review_cycle=1,
        boundary_ms=0,
        completed_at_ms=1,
        evidence_records=[],
    )
    assert result.status == "failed"
    assert result.strategy == state


def test_confidence_calibration_is_applied_once_and_clamped() -> None:
    assert adjust_confidence(0.8, side="long", strategy={"confidence_calibration": {"long": 0.1}}) == 0.9
    assert adjust_confidence(0.99, side="short", strategy={"confidence_calibration": {"short": 0.1}}) == 1.0


def test_evidence_must_be_closed_before_cutoff() -> None:
    try:
        validate_evidence_ids([{"episode_id": "e", "status": "closed", "closed_at_ms": 10}], ["e"], cutoff_ms=9)
    except ReviewerError:
        return
    raise AssertionError("future evidence was accepted")
