"""Offline Testnet execution-audit calculations and failure recovery."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


class AuditError(ValueError):
    """Raised when a Testnet audit fixture is incomplete or unsafe."""


@dataclass(frozen=True)
class AuditFill:
    side: str
    requested_size: Decimal
    filled_size: Decimal
    average_price: Decimal
    filled_at_ms: int


@dataclass(frozen=True)
class AuditTimeline:
    submitted_at_ms: int
    filled_at_ms: int
    protection_submitted_at_ms: int
    protection_confirmed_at_ms: int | None
    closed_at_ms: int | None


def worst_case_entry_price(*, side: str, allowed_price: Decimal, fill: AuditFill) -> Decimal:
    if fill.filled_size <= 0 or fill.average_price <= 0:
        raise AuditError("fill must have positive price and size")
    if side == "long":
        return max(allowed_price, fill.average_price)
    if side == "short":
        return min(allowed_price, fill.average_price)
    raise AuditError("side must be long or short")


def actual_stop_risk(*, side: str, entry_price: Decimal, stop_price: Decimal) -> Decimal:
    if entry_price <= 0 or stop_price <= 0:
        raise AuditError("prices must be positive")
    return (entry_price - stop_price).copy_abs() / entry_price * Decimal("100")


def protection_size_for_fill(*, requested_size: Decimal, filled_size: Decimal) -> Decimal:
    if requested_size < 0 or filled_size < 0 or filled_size > requested_size:
        raise AuditError("invalid fill size")
    return filled_size


def max_hold_deadline(*, filled_at_ms: int, max_hold_ms: int) -> int:
    if filled_at_ms < 0 or max_hold_ms <= 0:
        raise AuditError("invalid max hold inputs")
    return filled_at_ms + max_hold_ms


class RecoveryExchange(Protocol):
    def cancel_open_orders(self) -> None: ...
    def emergency_close(self) -> None: ...
    def position_size(self) -> Decimal: ...


def recover_protection_failure(exchange: RecoveryExchange) -> str:
    """Cancel, emergency-close, then require a flat position."""

    exchange.cancel_open_orders()
    exchange.emergency_close()
    if exchange.position_size() != 0:
        raise AuditError("emergency close did not leave a flat position")
    return "flat"


def audit_fixture(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a deterministic execution fixture without claiming profitability."""

    fill = AuditFill(
        side=str(payload["side"]),
        requested_size=Decimal(str(payload["requested_size"])),
        filled_size=Decimal(str(payload["filled_size"])),
        average_price=Decimal(str(payload["average_price"])),
        filled_at_ms=int(payload["filled_at_ms"]),
    )
    if payload.get("protection_confirmed_at_ms") is None:
        raise AuditError("protection was not confirmed")
    deadline = max_hold_deadline(filled_at_ms=fill.filled_at_ms, max_hold_ms=int(payload["max_hold_ms"]))
    closed_at = payload.get("closed_at_ms")
    if closed_at is not None and int(closed_at) > deadline:
        raise AuditError("fixture exceeded max hold")
    return {
        "status": "ok",
        "average_price": format(fill.average_price, "f"),
        "filled_size": format(fill.filled_size, "f"),
        "max_hold_deadline_ms": deadline,
        "pnl_claim": "not_evaluated",
    }
