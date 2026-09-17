"""Serialize only the current known account and market state for a policy."""

from __future__ import annotations

from typing import Any

from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import RiskLimits
from trading_core.market_data.models import MarketTick


def build_observation(
    snapshot: AccountSnapshot,
    market: MarketTick,
    limits: RiskLimits,
    *,
    previous_status: str | None,
) -> dict[str, Any]:
    """Return a JSON-ready snapshot without future prices or configurable Risk knobs."""

    return {
        "as_of_ms": market.timestamp_ms,
        "mark_price": str(market.close_price),
        "position": {
            "version": snapshot.position_version,
            "signed_quantity": str(snapshot.signed_quantity),
            "average_entry_price": str(snapshot.average_entry_price) if snapshot.average_entry_price else None,
            "stop_price": str(snapshot.stop_price) if snapshot.stop_price else None,
            "opened_at_ms": snapshot.opened_at_ms,
        },
        "account": {
            "equity": str(snapshot.equity),
            "unrealized_pnl": str(snapshot.unrealized_pnl),
            "daily_realized_pnl": str(snapshot.daily_realized_pnl),
        },
        "limits": {
            "exposure_anchor_usd": str(limits.exposure_anchor_usd),
            "max_hold_ms": limits.max_hold_ms,
        },
        "previous_status": previous_status,
    }
