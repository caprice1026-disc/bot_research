from __future__ import annotations

from collections.abc import Iterable
from typing import Any


MINIMUM_SECONDS = 9 * 30 * 24 * 60 * 60


def assess_walk_forward(events: Iterable[Any]) -> dict[str, Any]:
    """Check whether the minimum 6/1/2-month walk-forward window is present."""

    event_list = list(events)
    if not event_list:
        return {
            "status": "insufficient_data",
            "reason": "requires 9 months of timestamped events for train/validation/test folds",
            "observed_seconds": 0,
        }
    timestamps = [int(event.timestamp) for event in event_list]
    observed_seconds = max(timestamps) - min(timestamps)
    if observed_seconds < MINIMUM_SECONDS:
        return {
            "status": "insufficient_data",
            "reason": "requires 9 months of timestamped events for train/validation/test folds",
            "observed_seconds": observed_seconds,
        }
    return {
        "status": "ready",
        "reason": "minimum 9-month window is available; fold construction can proceed",
        "observed_seconds": observed_seconds,
        "folds": {"train_months": 6, "validation_months": 1, "test_months": 2},
    }
