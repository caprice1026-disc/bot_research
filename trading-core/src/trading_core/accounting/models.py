"""Decimal-safe account state shared by planners and simulators."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class AccountingError(ValueError):
    """Raised when a persisted or simulated account snapshot is inconsistent."""


def _finite(value: Decimal, *, name: str) -> None:
    if not value.is_finite():
        raise AccountingError(f"{name} must be finite")


@dataclass(frozen=True)
class AccountSnapshot:
    """One signed BTC position and the cash values known at a UTC instant."""

    position_version: int
    signed_quantity: Decimal
    average_entry_price: Decimal | None
    cash: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    opened_at_ms: int | None
    stop_price: Decimal | None
    pending_order_ids: tuple[str, ...]
    day_start_equity: Decimal
    daily_realized_pnl: Decimal
    peak_equity: Decimal
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    funding_paid: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.position_version < 0:
            raise AccountingError("position_version must be non-negative")
        if self.opened_at_ms is not None and self.opened_at_ms < 0:
            raise AccountingError("opened_at_ms must be non-negative")
        for name in (
            "signed_quantity",
            "cash",
            "equity",
            "unrealized_pnl",
            "day_start_equity",
            "daily_realized_pnl",
            "peak_equity",
            "realized_pnl",
            "fees_paid",
            "funding_paid",
        ):
            _finite(getattr(self, name), name=name)
        for name in ("average_entry_price", "stop_price"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name=name)
                if value <= 0:
                    raise AccountingError(f"{name} must be positive")
        if self.signed_quantity == 0 and self.average_entry_price is not None:
            raise AccountingError("flat positions cannot have an average entry price")
        if self.signed_quantity == 0 and self.opened_at_ms is not None:
            raise AccountingError("flat positions cannot have an opening timestamp")
