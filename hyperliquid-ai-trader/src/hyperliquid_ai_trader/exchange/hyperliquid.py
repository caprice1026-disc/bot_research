"""Official Hyperliquid Python SDK adapter."""

from __future__ import annotations

from decimal import Decimal
import hashlib
import os
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import TESTNET_API_URL
from hyperliquid.utils.types import Cloid

from ..config import Settings
from ..models import BookLevel, Candle, OrderPlan, Side
from ..proxy import clear_invalid_loopback_proxies
from .base import BracketResult, ExchangeAccountSnapshot, MarketObservation


def make_cloids(run_id: str, slot: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for leg in ("entry", "tp", "sl"):
        digest = hashlib.blake2b(
            f"hl-ai-trader-v1:{run_id}:{slot}:{leg}".encode("utf-8"),
            digest_size=16,
        ).hexdigest()
        result[leg] = f"0x{digest}"
    return result


def _round_price(price: Decimal) -> float:
    return float(f"{float(price):.5g}")


def create_hyperliquid_adapter(settings: Settings) -> "HyperliquidAdapter":
    clear_invalid_loopback_proxies(os.environ)
    wallet = Account.from_key(settings.private_key)
    info = Info(TESTNET_API_URL, skip_ws=True, timeout=15.0)
    exchange = Exchange(
        wallet,
        TESTNET_API_URL,
        account_address=settings.wallet_address,
        timeout=15.0,
    )
    return HyperliquidAdapter(
        info_client=info,
        exchange_client=exchange,
        account_address=settings.wallet_address,
    )


class HyperliquidAdapter:
    def __init__(self, *, info_client: Any, exchange_client: Any, account_address: str) -> None:
        self.info = info_client
        self.exchange = exchange_client
        self.account_address = account_address
        for client in (info_client, exchange_client):
            session = getattr(client, "session", None)
            if session is not None:
                session.trust_env = False

    def get_market_observation(self, coin: str, *, now_ms: int) -> MarketObservation:
        meta, contexts = self.info.meta_and_asset_ctxs()
        universe = meta.get("universe", [])
        index = next((i for i, asset in enumerate(universe) if asset.get("name") == coin), None)
        if index is None or index >= len(contexts):
            raise ValueError(f"coin is not available on Hyperliquid Testnet: {coin}")
        asset = universe[index]
        context = contexts[index]
        raw_candles = self.info.candles_snapshot(
            coin,
            "1m",
            now_ms - 121 * 60_000,
            now_ms,
        )
        candles = [
            Candle(
                timestamp_ms=int(item["t"]),
                open=float(item["o"]),
                high=float(item["h"]),
                low=float(item["l"]),
                close=float(item["c"]),
                volume=float(item["v"]),
            )
            for item in raw_candles
        ]
        book = self.info.l2_snapshot(coin)
        levels = book.get("levels", [])
        if not isinstance(levels, list) or len(levels) != 2:
            raise ValueError("unexpected l2Book response")
        bids = [BookLevel(price=float(item["px"]), size=float(item["sz"])) for item in levels[0]]
        asks = [BookLevel(price=float(item["px"]), size=float(item["sz"])) for item in levels[1]]
        return MarketObservation(
            candles=candles,
            bids=bids,
            asks=asks,
            mark=Decimal(str(context["markPx"])),
            oracle=Decimal(str(context["oraclePx"])),
            funding=Decimal(str(context["funding"])),
            open_interest=Decimal(str(context["openInterest"])),
            size_decimals=int(asset["szDecimals"]),
        )

    def get_account_snapshot(self, coin: str) -> ExchangeAccountSnapshot:
        state = self.info.user_state(self.account_address)
        margin = state.get("marginSummary", {})
        position_size = Decimal("0")
        entry_price: Decimal | None = None
        unrealized_pnl = Decimal("0")
        unknown_exposure = False
        for wrapper in state.get("assetPositions", []):
            position = wrapper.get("position", {})
            candidate_size = Decimal(str(position.get("szi", "0")))
            if position.get("coin") == coin:
                position_size = candidate_size
                raw_entry = position.get("entryPx")
                entry_price = Decimal(str(raw_entry)) if raw_entry is not None else None
                unrealized_pnl = Decimal(str(position.get("unrealizedPnl", "0")))
            elif candidate_size != 0:
                unknown_exposure = True
        return ExchangeAccountSnapshot(
            equity=Decimal(str(margin.get("accountValue", "0"))),
            withdrawable=Decimal(str(state.get("withdrawable", "0"))),
            position_size=position_size,
            entry_price=entry_price,
            unrealized_pnl=unrealized_pnl,
            open_orders=list(self.info.frontend_open_orders(self.account_address)),
            unknown_exposure=unknown_exposure,
        )

    def set_leverage(self, coin: str, leverage: int, margin_mode: str) -> None:
        response = self.exchange.update_leverage(leverage, coin, margin_mode == "cross")
        if not isinstance(response, dict) or response.get("status") != "ok":
            raise RuntimeError("leverage update was rejected")

    def place_bracket(
        self,
        *,
        coin: str,
        plan: OrderPlan,
        run_id: str,
        slot: int,
        max_entry_slippage_bps: Decimal,
    ) -> BracketResult:
        cloids = make_cloids(run_id, slot)
        is_buy = plan.side is Side.LONG
        slippage = max_entry_slippage_bps / Decimal("10000")
        entry_limit = plan.entry_price * (Decimal("1") + slippage if is_buy else Decimal("1") - slippage)
        opposite = not is_buy

        requests = [
            {
                "coin": coin,
                "is_buy": is_buy,
                "sz": float(plan.size),
                "limit_px": _round_price(entry_limit),
                "order_type": {"limit": {"tif": "Ioc"}},
                "reduce_only": False,
                "cloid": Cloid.from_str(cloids["entry"]),
            },
            {
                "coin": coin,
                "is_buy": opposite,
                "sz": float(plan.size),
                "limit_px": _round_price(plan.take_profit_price),
                "order_type": {
                    "trigger": {
                        "triggerPx": _round_price(plan.take_profit_price),
                        "isMarket": True,
                        "tpsl": "tp",
                    }
                },
                "reduce_only": True,
                "cloid": Cloid.from_str(cloids["tp"]),
            },
            {
                "coin": coin,
                "is_buy": opposite,
                "sz": float(plan.size),
                "limit_px": _round_price(plan.stop_loss_price),
                "order_type": {
                    "trigger": {
                        "triggerPx": _round_price(plan.stop_loss_price),
                        "isMarket": True,
                        "tpsl": "sl",
                    }
                },
                "reduce_only": True,
                "cloid": Cloid.from_str(cloids["sl"]),
            },
        ]

        response = self.exchange.bulk_orders(requests, grouping="normalTpsl")
        if not isinstance(response, dict) or response.get("status") != "ok":
            return BracketResult(False, False, "exchange_rejected", cloids)
        data = response.get("response", {}).get("data", {})
        statuses = data.get("statuses") if isinstance(data, dict) else None
        if not isinstance(statuses, list) or len(statuses) != 3:
            return BracketResult(False, True, "ambiguous_response", cloids)

        entry_status = statuses[0]
        filled = entry_status.get("filled") if isinstance(entry_status, dict) else None
        if not isinstance(filled, dict):
            return BracketResult(False, False, "entry_not_filled", cloids, statuses)
        filled_size = Decimal(str(filled.get("totalSz", "0")))
        if filled_size != plan.size:
            return BracketResult(
                False,
                True,
                "partial_fill",
                cloids,
                statuses,
                filled_size,
            )
        if any(not isinstance(status, dict) or "resting" not in status for status in statuses[1:]):
            return BracketResult(
                False,
                True,
                "protection_rejected",
                cloids,
                statuses,
                filled_size,
            )
        return BracketResult(True, False, None, cloids, statuses, filled_size)

    def close_position(self, coin: str) -> dict[str, Any] | None:
        return self.exchange.market_close(coin)

    def cancel_bot_orders(self, coin: str, cloids: list[str]) -> list[dict[str, Any]]:
        return [self.exchange.cancel_by_cloid(coin, Cloid.from_str(cloid)) for cloid in cloids]

    def query_order(self, cloid: str) -> Any:
        return self.info.query_order_by_cloid(self.account_address, Cloid.from_str(cloid))

    def get_user_fills(self, start_time_ms: int, end_time_ms: int) -> list[dict[str, Any]]:
        return list(
            self.info.user_fills_by_time(
                self.account_address,
                start_time_ms,
                end_time_ms,
                aggregate_by_time=True,
            )
        )

    def get_user_funding(self, start_time_ms: int, end_time_ms: int) -> list[dict[str, Any]]:
        return list(self.info.user_funding_history(self.account_address, start_time_ms, end_time_ms))
