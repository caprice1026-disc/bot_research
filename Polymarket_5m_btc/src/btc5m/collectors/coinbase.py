from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from btc5m.clock import ReceiveStamp
from btc5m.events import RawEvent


def _decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def parse_coinbase_message(
    payload: Mapping[str, Any],
    received: ReceiveStamp,
) -> list[RawEvent]:
    channel = str(payload.get("channel") or "")
    outer_timestamp = payload.get("timestamp")
    sequence = payload.get("sequence_num")
    output: list[RawEvent] = []
    for event in payload.get("events", []):
        if not isinstance(event, Mapping):
            continue
        if channel == "ticker":
            for ticker in event.get("tickers", []):
                if not isinstance(ticker, Mapping):
                    continue
                product = str(ticker.get("product_id") or "")
                if not product:
                    continue
                output.append(
                    RawEvent.from_message(
                        source="coinbase",
                        symbol=product,
                        event_type="ticker",
                        payload=dict(payload),
                        received=received,
                        source_event_ts=ticker.get("time") or outer_timestamp,
                        source_publish_ts=outer_timestamp,
                        sequence_id=f"{sequence}:{product}"
                        if sequence is not None
                        else None,
                        price=_decimal(ticker.get("price")),
                        bid=_decimal(ticker.get("best_bid")),
                        ask=_decimal(ticker.get("best_ask")),
                        bid_size=_decimal(ticker.get("best_bid_quantity")),
                        ask_size=_decimal(ticker.get("best_ask_quantity")),
                    )
                )
        elif channel == "market_trades":
            for trade in event.get("trades", []):
                if not isinstance(trade, Mapping):
                    continue
                product = str(trade.get("product_id") or "")
                if not product:
                    continue
                trade_id = trade.get("trade_id")
                output.append(
                    RawEvent.from_message(
                        source="coinbase",
                        symbol=product,
                        event_type="market_trade",
                        payload=dict(payload),
                        received=received,
                        source_event_ts=trade.get("time") or outer_timestamp,
                        source_publish_ts=outer_timestamp,
                        sequence_id=str(trade_id) if trade_id is not None else None,
                        price=_decimal(trade.get("price")),
                        bid=None,
                        ask=None,
                        bid_size=None,
                        ask_size=None,
                    )
                )
    return output
