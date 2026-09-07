from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from btc5m.clock import ReceiveStamp
from btc5m.events import RawEvent


def _decimal(value: object) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def build_chainlink_specs() -> tuple[object, object]:
    from polymarket.streams import CryptoPricesChainlinkTwapSpec

    return (
        CryptoPricesChainlinkTwapSpec(window_seconds=30, symbols=["btc/usd"]),
        CryptoPricesChainlinkTwapSpec(window_seconds=60, symbols=["btc/usd"]),
    )


def parse_chainlink_message(
    payload: Mapping[str, Any],
    received: ReceiveStamp,
    window_seconds: int,
) -> list[RawEvent]:
    if window_seconds not in (30, 60):
        raise ValueError("window_seconds must be 30 or 60")
    body = payload.get("payload")
    if not isinstance(body, Mapping):
        body = payload
    symbol = str(body.get("symbol") or "")
    if symbol.lower() != "btc/usd":
        return []
    value = body.get("value") or body.get("full_accuracy_value") or body.get("price")
    if value in (None, ""):
        return []
    return [
        RawEvent.from_message(
            source="chainlink",
            symbol=symbol,
            event_type=f"chainlink_twap_{window_seconds}",
            payload=dict(payload),
            received=received,
            source_event_ts=body.get("timestamp"),
            source_publish_ts=payload.get("timestamp") if body is not payload else None,
            sequence_id=None,
            price=_decimal(value),
            bid=None,
            ask=None,
            bid_size=None,
            ask_size=None,
        )
    ]
