from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from btc5m.clock import ReceiveStamp

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _datetime_to_epoch_us(value: datetime) -> int:
    normalized = (
        value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    )
    normalized = normalized.astimezone(timezone.utc)
    delta = normalized - _EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def parse_source_timestamp(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _datetime_to_epoch_us(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = Decimal(text)
        except Exception:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return _datetime_to_epoch_us(parsed)
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        magnitude = abs(number)
        if magnitude >= Decimal("1e17"):
            return int(number / Decimal(1000))
        if magnitude >= Decimal("1e14"):
            return int(number)
        if magnitude >= Decimal("1e11"):
            return int(number * Decimal(1000))
        return int(number * Decimal(1_000_000))
    raise TypeError(f"unsupported timestamp type: {type(value).__name__}")


def format_utc_timestamp(value: datetime) -> str:
    normalized = (
        value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    )
    return (
        normalized.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True, slots=True)
class RawEvent:
    source: str
    symbol: str
    event_type: str
    source_event_ts: int | None
    source_publish_ts: int | None
    local_receive_ts: datetime | None
    local_monotonic_ns: int | None
    sequence_id: str | None
    price: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    raw_payload: Mapping[str, Any]

    @classmethod
    def from_message(
        cls,
        *,
        source: str,
        symbol: str,
        event_type: str,
        payload: Mapping[str, Any],
        received: ReceiveStamp,
        source_event_ts: object,
        source_publish_ts: object,
        sequence_id: str | None,
        price: Decimal | None,
        bid: Decimal | None,
        ask: Decimal | None,
        bid_size: Decimal | None,
        ask_size: Decimal | None,
    ) -> RawEvent:
        if not source:
            raise ValueError("source must be non-empty")
        if not event_type:
            raise ValueError("event_type must be non-empty")
        return cls(
            source=source,
            symbol=symbol,
            event_type=event_type,
            source_event_ts=parse_source_timestamp(source_event_ts),
            source_publish_ts=parse_source_timestamp(source_publish_ts),
            local_receive_ts=received.local_receive_ts,
            local_monotonic_ns=received.local_monotonic_ns,
            sequence_id=sequence_id,
            price=price,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            raw_payload=dict(payload),
        )

    @classmethod
    def from_historical(
        cls,
        *,
        source: str,
        symbol: str,
        event_type: str,
        payload: Mapping[str, Any],
        source_event_ts: object,
        source_publish_ts: object = None,
        sequence_id: str | None = None,
        price: Decimal | None = None,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
        bid_size: Decimal | None = None,
        ask_size: Decimal | None = None,
    ) -> RawEvent:
        """Build an event without inventing a local receive timestamp.

        Historical archives contain source timestamps but no observation time at
        our collector.  Keeping the local fields null prevents event-time data
        from being mistaken for executable receive-time data.
        """
        if not source:
            raise ValueError("source must be non-empty")
        if not event_type:
            raise ValueError("event_type must be non-empty")
        return cls(
            source=source,
            symbol=symbol,
            event_type=event_type,
            source_event_ts=parse_source_timestamp(source_event_ts),
            source_publish_ts=parse_source_timestamp(source_publish_ts),
            local_receive_ts=None,
            local_monotonic_ns=None,
            sequence_id=sequence_id,
            price=price,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            raw_payload=dict(payload),
        )

    def to_row(self) -> dict[str, object]:
        return {
            "source": self.source,
            "symbol": self.symbol,
            "event_type": self.event_type,
            "source_event_ts": self.source_event_ts,
            "source_publish_ts": self.source_publish_ts,
            "local_receive_ts": (
                None
                if self.local_receive_ts is None
                else format_utc_timestamp(self.local_receive_ts)
            ),
            "local_monotonic_ns": self.local_monotonic_ns,
            "sequence_id": self.sequence_id,
            "price": _decimal_text(self.price),
            "bid": _decimal_text(self.bid),
            "ask": _decimal_text(self.ask),
            "bid_size": _decimal_text(self.bid_size),
            "ask_size": _decimal_text(self.ask_size),
            "raw_payload": json.dumps(
                self.raw_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        }
