"""Market values that are safe to hand to a position planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping


class MarketDataError(ValueError):
    """Raised when a market observation is invalid or time-inconsistent."""


def _positive(value: Decimal, *, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise MarketDataError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class MarketSnapshot:
    """A decision-time mark and only features already available at that time."""

    as_of_ms: int
    available_at_ms: int
    mark_price: Decimal
    quantity_step: Decimal
    features: Mapping[str, str | int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.as_of_ms < 0 or self.available_at_ms < 0:
            raise MarketDataError("market timestamps must be non-negative")
        if self.available_at_ms > self.as_of_ms:
            raise MarketDataError("market snapshot uses unavailable future data")
        _positive(self.mark_price, name="mark_price")
        _positive(self.quantity_step, name="quantity_step")


@dataclass(frozen=True)
class MarketTick:
    """One completed minute used by the stateful simulator after a decision."""

    timestamp_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise MarketDataError("tick timestamp must be non-negative")
        for name in ("open_price", "high_price", "low_price", "close_price"):
            _positive(getattr(self, name), name=name)
        if self.high_price < max(self.open_price, self.close_price):
            raise MarketDataError("high_price is below open or close")
        if self.low_price > min(self.open_price, self.close_price):
            raise MarketDataError("low_price is above open or close")
