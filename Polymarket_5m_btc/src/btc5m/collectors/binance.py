from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from btc5m.clock import ReceiveStamp
from btc5m.events import RawEvent


def _decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _unwrap(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("data")
    return nested if isinstance(nested, Mapping) else payload


def parse_binance_message(
    payload: Mapping[str, Any],
    received: ReceiveStamp,
) -> list[RawEvent]:
    stream_name = str(payload.get("stream") or "").lower()
    message = _unwrap(payload)
    event_name = message.get("e")
    if event_name is None and (
        stream_name.endswith("@bookticker")
        or all(field in message for field in ("u", "s", "b", "B", "a", "A"))
    ):
        event_name = "bookTicker"
    symbol = str(message.get("s") or stream_name.split("@", 1)[0].upper() or "BTCUSDT")
    if event_name == "aggTrade":
        return [
            RawEvent.from_message(
                source="binance",
                symbol=symbol,
                event_type="agg_trade",
                payload=payload,
                received=received,
                source_event_ts=message.get("T"),
                source_publish_ts=message.get("E"),
                sequence_id=str(message["a"]) if message.get("a") is not None else None,
                price=_decimal(message.get("p")),
                bid=None,
                ask=None,
                bid_size=None,
                ask_size=None,
            )
        ]
    if event_name == "bookTicker":
        return [
            RawEvent.from_message(
                source="binance",
                symbol=symbol,
                event_type="book_ticker",
                payload=payload,
                received=received,
                source_event_ts=message.get("E"),
                source_publish_ts=None,
                sequence_id=str(message["u"]) if message.get("u") is not None else None,
                price=None,
                bid=_decimal(message.get("b")),
                ask=_decimal(message.get("a")),
                bid_size=_decimal(message.get("B")),
                ask_size=_decimal(message.get("A")),
            )
        ]
    return []
