"""Domain models shared across adapters, agents, and storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Candle:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class MarketFeatures:
    mid: float
    mark: float
    oracle: float
    funding: float
    open_interest: float
    return_1m: float
    return_5m: float
    return_15m: float
    return_60m: float
    realized_vol_5m: float
    realized_vol_30m: float
    atr_pct: float
    spread_bps: float
    book_imbalance: float
    volume_zscore: float

    def to_prompt_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TradeDecision:
    side: Side
    stop_loss_pct: Decimal
    take_profit_pct: Decimal
    confidence: Decimal
    thesis: str
    would_abstain: bool
    abstain_reason: str | None


@dataclass(frozen=True)
class AccountState:
    equity: Decimal
    withdrawable: Decimal
    day_start_equity: Decimal
    daily_realized_pnl: Decimal
    session_peak_equity: Decimal
    position_size: Decimal = Decimal("0")
    open_order_count: int = 0


@dataclass(frozen=True)
class MarketSnapshot:
    entry_price: Decimal
    size_decimals: int


@dataclass(frozen=True)
class OrderPlan:
    side: Side
    size: Decimal
    notional: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    risk_budget_usd: Decimal
    decision: TradeDecision
