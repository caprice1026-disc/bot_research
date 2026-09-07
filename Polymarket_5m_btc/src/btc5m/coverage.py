from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from btc5m.events import parse_source_timestamp


def coverage_channel(row: Mapping[str, object]) -> str:
    """Return the observable channel used for continuity checks.

    Polymarket book snapshots and quote-bearing price changes are one quote
    series for coverage purposes.  Trade and resolver windows remain separate
    so a quiet trade feed cannot hide a missing quote or Chainlink feed.
    """

    source = str(row.get("source") or "")
    event_type = str(row.get("event_type") or "")
    if source == "polymarket" and event_type in {"book", "best_bid_ask"}:
        return "quote"
    if source == "polymarket" and event_type == "price_change":
        if row.get("bid") not in (None, "") and row.get("ask") not in (None, ""):
            return "quote"
        return "price_change_unquoted"
    return event_type


@dataclass(frozen=True, slots=True)
class GapInterval:
    source: str
    channel: str
    symbol: str
    market_id: str
    start_ts: int
    end_ts: int
    reason: str = "receive_gap"
    previous_connection_id: str | None = None
    current_connection_id: str | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end_ts - self.start_ts) / 1_000_000

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "channel": self.channel,
            "symbol": self.symbol,
            "market_id": self.market_id,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "duration_seconds": round(self.duration_seconds, 6),
            "reason": self.reason,
            "previous_connection_id": self.previous_connection_id,
            "current_connection_id": self.current_connection_id,
        }


@dataclass(frozen=True, slots=True)
class _Observation:
    timestamp: int
    connection_id: str | None


class CoverageTracker:
    """Track receive gaps without filling or imputing missing observations."""

    def __init__(self, *, max_gap_seconds: float = 5.0) -> None:
        if max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        self.max_gap_us = int(max_gap_seconds * 1_000_000)
        self._last: dict[tuple[str, str, str, str], _Observation] = {}
        self.gaps: list[GapInterval] = []
        self.inversion_count = 0

    @staticmethod
    def _key(row: Mapping[str, object]) -> tuple[str, str, str, str]:
        return (
            str(row.get("source") or ""),
            coverage_channel(row),
            str(row.get("symbol") or ""),
            str(row.get("market_id") or ""),
        )

    def observe(
        self,
        row: Mapping[str, object],
        *,
        connection_id: str | None = None,
    ) -> None:
        receive_ts = parse_source_timestamp(row.get("local_receive_ts"))
        if receive_ts is None:
            return
        key = self._key(row)
        previous = self._last.get(key)
        if previous is not None:
            delta = receive_ts - previous.timestamp
            if delta < 0:
                self.inversion_count += 1
            elif delta > self.max_gap_us:
                self.gaps.append(
                    GapInterval(
                        source=key[0],
                        channel=key[1],
                        symbol=key[2],
                        market_id=key[3],
                        start_ts=previous.timestamp,
                        end_ts=receive_ts,
                        previous_connection_id=previous.connection_id,
                        current_connection_id=connection_id
                        or str(row.get("connection_id") or "")
                        or None,
                    )
                )
        if previous is None or receive_ts >= previous.timestamp:
            self._last[key] = _Observation(receive_ts, connection_id)

    def finish(self, end_ts: int, *, reason: str = "collection_end") -> None:
        for key, previous in self._last.items():
            if end_ts - previous.timestamp > self.max_gap_us:
                self.gaps.append(
                    GapInterval(
                        source=key[0],
                        channel=key[1],
                        symbol=key[2],
                        market_id=key[3],
                        start_ts=previous.timestamp,
                        end_ts=end_ts,
                        reason=reason,
                        previous_connection_id=previous.connection_id,
                    )
                )

    def to_dicts(self) -> list[dict[str, object]]:
        return [gap.to_dict() for gap in self.gaps]


def gap_overlaps(
    gap: Mapping[str, object],
    *,
    start_ts: int,
    end_ts: int,
    source: str | None = None,
    channel: str | None = None,
    symbol: str | None = None,
    market_id: str | None = None,
) -> bool:
    """Return whether a gap applies to a requested channel/time interval."""

    if source and str(gap.get("source") or "") != source:
        return False
    if channel and str(gap.get("channel") or "") != channel:
        return False
    if symbol and str(gap.get("symbol") or "") not in {"", symbol}:
        return False
    if market_id and str(gap.get("market_id") or "") not in {"", market_id}:
        return False
    raw_start = gap.get("start_ts")
    raw_end = gap.get("end_ts")
    try:
        gap_start = int(str(raw_start)) if raw_start is not None else 0
        gap_end = int(str(raw_end)) if raw_end is not None else 0
    except (TypeError, ValueError):
        return False
    return start_ts < gap_end and gap_start < end_ts
