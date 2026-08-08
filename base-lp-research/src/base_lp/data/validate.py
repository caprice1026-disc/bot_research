from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from base_lp.schemas import LogRecord


@dataclass(frozen=True)
class LogOrderReport:
    valid: bool
    duplicate_keys: list[tuple[int, int, int]] = field(default_factory=list)
    out_of_order_keys: list[tuple[int, int, int]] = field(default_factory=list)


def validate_log_order(logs: Iterable[LogRecord]) -> LogOrderReport:
    previous: tuple[int, int, int] | None = None
    seen: set[tuple[int, int, int]] = set()
    duplicates: list[tuple[int, int, int]] = []
    out_of_order: list[tuple[int, int, int]] = []
    for log in logs:
        key = log.stable_key
        if key in seen:
            duplicates.append(key)
        if previous is not None and key <= previous:
            out_of_order.append(key)
        seen.add(key)
        previous = key
    return LogOrderReport(
        valid=not duplicates and not out_of_order,
        duplicate_keys=duplicates,
        out_of_order_keys=out_of_order,
    )
