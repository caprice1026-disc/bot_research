"""Exchange-neutral contracts used by the trading service."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from ..models import OrderPlan
from ..models import BookLevel, Candle


@dataclass(frozen=True)
class BracketResult:
    success: bool
    requires_recovery: bool
    error_type: str | None
    cloids: dict[str, str]
    statuses: list[dict[str, Any]] = field(default_factory=list)
    filled_size: Decimal = Decimal("0")


@dataclass(frozen=True)
class MarketObservation:
    candles: list[Candle]
    bids: list[BookLevel]
    asks: list[BookLevel]
    mark: Decimal
    oracle: Decimal
    funding: Decimal
    open_interest: Decimal
    size_decimals: int


@dataclass(frozen=True)
class ExchangeAccountSnapshot:
    equity: Decimal
    withdrawable: Decimal
    position_size: Decimal
    entry_price: Decimal | None
    unrealized_pnl: Decimal
    open_orders: list[dict[str, Any]]
    unknown_exposure: bool = False
    spot_usdc: Decimal = Decimal("0")
    account_mode: str = "default"
    collateral_source: str = "perpClearinghouseState"


class TradingExchange(Protocol):
    def get_market_observation(self, coin: str, *, now_ms: int) -> MarketObservation: ...

    def get_account_snapshot(self, coin: str) -> ExchangeAccountSnapshot: ...

    def transfer_usd_class(self, amount: Decimal, *, to_perp: bool) -> dict[str, Any]: ...

    def set_leverage(self, coin: str, leverage: int, margin_mode: str) -> None: ...

    def place_bracket(
        self,
        *,
        coin: str,
        plan: OrderPlan,
        run_id: str,
        slot: int,
        max_entry_slippage_bps: Decimal,
    ) -> BracketResult: ...

    def close_position(self, coin: str) -> dict[str, Any] | None: ...

    def cancel_bot_orders(self, coin: str, cloids: list[str]) -> list[dict[str, Any]]: ...

    def get_user_fills(self, start_time_ms: int, end_time_ms: int) -> list[dict[str, Any]]: ...

    def get_user_funding(self, start_time_ms: int, end_time_ms: int) -> list[dict[str, Any]]: ...
