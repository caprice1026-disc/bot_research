"""Paired static/adaptive forward evaluation with shared observation slots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


class ForwardError(ValueError):
    """Raised when paired forward inputs are not identical."""


@dataclass(frozen=True)
class ForwardSlot:
    slot_id: str
    snapshot: Any
    static_value: Any
    adaptive_value: Any


def run_paired_forward(
    slots: Iterable[Mapping[str, Any]],
    *,
    static_strategy: Mapping[str, Any],
    adaptive_strategy: Mapping[str, Any],
    static_runner: Callable[[Mapping[str, Any], Any], Any],
    adaptive_runner: Callable[[Mapping[str, Any], Any], Any],
) -> tuple[ForwardSlot, ...]:
    """Run both arms on the same slots; a missed slot is never backfilled."""

    result: list[ForwardSlot] = []
    for slot in slots:
        if "slot_id" not in slot or "snapshot" not in slot:
            raise ForwardError("forward slot needs slot_id and snapshot")
        snapshot = slot["snapshot"]
        result.append(
            ForwardSlot(
                str(slot["slot_id"]),
                snapshot,
                static_runner(static_strategy, snapshot),
                adaptive_runner(adaptive_strategy, snapshot),
            )
        )
    return tuple(result)
