from __future__ import annotations

from csv import reader
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Sequence

from btc5m.events import RawEvent

_BINANCE_ARCHIVE_BASE = "https://data.binance.vision/data/spot/daily/aggTrades"


@dataclass(frozen=True, slots=True)
class BinanceCoverageManifest:
    symbol: str
    requested_days: tuple[date, ...]
    urls: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "requested_days": [item.isoformat() for item in self.requested_days],
            "urls": list(self.urls),
        }


def binance_daily_url(symbol: str, day: date) -> str:
    normalized = symbol.upper()
    return (
        f"{_BINANCE_ARCHIVE_BASE}/{normalized}/"
        f"{normalized}-aggTrades-{day:%Y-%m-%d}.zip"
    )


def requested_utc_days(start: datetime, end: datetime) -> list[date]:
    normalized_start = (
        start if start.tzinfo is not None else start.replace(tzinfo=timezone.utc)
    )
    normalized_end = end if end.tzinfo is not None else end.replace(tzinfo=timezone.utc)
    normalized_start = normalized_start.astimezone(timezone.utc)
    normalized_end = normalized_end.astimezone(timezone.utc)
    if normalized_end <= normalized_start:
        return []
    current = normalized_start.date()
    last = (normalized_end - timedelta(microseconds=1)).date()
    days: list[date] = []
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return days


def build_binance_coverage(
    start: datetime,
    end: datetime,
    *,
    symbol: str = "BTCUSDT",
) -> BinanceCoverageManifest:
    normalized = symbol.upper()
    days = tuple(requested_utc_days(start, end))
    return BinanceCoverageManifest(
        symbol=normalized,
        requested_days=days,
        urls=tuple(binance_daily_url(normalized, day) for day in days),
    )


def normalize_aggtrade_rows(rows: Iterable[Sequence[object]]) -> list[RawEvent]:
    """Normalize Binance spot aggTrades CSV rows into canonical historical events."""
    events: list[RawEvent] = []
    for row in rows:
        if not row or str(row[0]).lower() in {"agg_trade_id", "id"}:
            continue
        if len(row) < 7:
            raise ValueError(
                f"aggTrade row has {len(row)} columns; expected at least 7"
            )
        trade_id, price, quantity, first_id, last_id, timestamp, buyer_maker = row[:7]
        payload = {
            "a": str(trade_id),
            "p": str(price),
            "q": str(quantity),
            "f": str(first_id),
            "l": str(last_id),
            "T": str(timestamp),
            "m": str(buyer_maker).lower() == "true",
        }
        events.append(
            RawEvent.from_historical(
                source="binance",
                symbol="BTCUSDT",
                event_type="agg_trade",
                payload=payload,
                source_event_ts=timestamp,
                sequence_id=str(trade_id),
                price=Decimal(str(price)),
            )
        )
    return events


def normalize_aggtrade_csv(text: str) -> list[RawEvent]:
    return normalize_aggtrade_rows(reader(text.splitlines()))
