"""Point-in-Time availability checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _ensure_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trade_time must be timezone-aware")


def is_available_at_trade_time(record: Any, trade_time: datetime) -> bool:
    """Return whether every publication timestamp on a record is known by trade_time."""

    _ensure_aware(trade_time)
    for field_name in ("forecast_issue_time", "received_time", "timestamp"):
        timestamp = getattr(record, field_name, None)
        if timestamp is not None:
            _ensure_aware(timestamp)
            if timestamp > trade_time:
                return False
    return True


def filter_available_records(records: list[Any], trade_time: datetime) -> list[Any]:
    """Keep records whose publication timestamps do not leak future information."""

    return [record for record in records if is_available_at_trade_time(record, trade_time)]
