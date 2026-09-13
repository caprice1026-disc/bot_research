"""The single execution-cost definition shared by research inputs and fills."""

from __future__ import annotations

from decimal import Decimal

from ..models import Side


COST_MODEL_VERSION = "cost_v1"


def execution_price(price: Decimal, *, side: Side, entering: bool, config) -> Decimal:
    half_spread = config.spread_bps / Decimal("2")
    adverse_bps = half_spread + config.slippage_bps
    is_buy = (side is Side.LONG) == entering
    factor = Decimal("1") + (adverse_bps if is_buy else -adverse_bps) / Decimal("10000")
    return price * factor


def estimated_round_trip_cost_bps(config) -> Decimal:
    return config.spread_bps + (config.slippage_bps * Decimal("2")) + (config.fee_rate * Decimal("2") * Decimal("10000"))
