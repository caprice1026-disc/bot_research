from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from btc5m.clock import ReceiveStamp
from btc5m.events import RawEvent


def _decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def parse_hyperliquid_message(
    payload: Mapping[str, Any],
    received: ReceiveStamp,
) -> list[RawEvent]:
    channel = str(payload.get("channel") or "")
    data = payload.get("data")
    output: list[RawEvent] = []
    if channel == "trades" and isinstance(data, list):
        for trade in data:
            if not isinstance(trade, Mapping):
                continue
            output.append(
                RawEvent.from_message(
                    source="hyperliquid",
                    symbol=str(trade.get("coin") or "BTC"),
                    event_type="trade",
                    payload=dict(payload),
                    received=received,
                    source_event_ts=trade.get("time"),
                    source_publish_ts=None,
                    sequence_id=str(trade["tid"])
                    if trade.get("tid") is not None
                    else None,
                    price=_decimal(trade.get("px")),
                    bid=None,
                    ask=None,
                    bid_size=None,
                    ask_size=None,
                )
            )
    elif channel == "bbo" and isinstance(data, Mapping):
        levels = data.get("bbo")
        if isinstance(levels, list) and len(levels) >= 2:
            bid_level, ask_level = levels[0], levels[1]
            if isinstance(bid_level, Mapping) and isinstance(ask_level, Mapping):
                output.append(
                    RawEvent.from_message(
                        source="hyperliquid",
                        symbol=str(data.get("coin") or "BTC"),
                        event_type="bbo",
                        payload=dict(payload),
                        received=received,
                        source_event_ts=data.get("time"),
                        source_publish_ts=None,
                        sequence_id=None,
                        price=None,
                        bid=_decimal(bid_level.get("px")),
                        ask=_decimal(ask_level.get("px")),
                        bid_size=_decimal(bid_level.get("sz")),
                        ask_size=_decimal(ask_level.get("sz")),
                    )
                )
    return output
