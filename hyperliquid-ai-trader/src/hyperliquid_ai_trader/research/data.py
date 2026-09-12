"""Time-safe one-minute candle normalization and common OHLCV features."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from math import isfinite
from pathlib import Path
from statistics import pstdev
from typing import Any


CANDLE_INTERVAL_MS = 60_000
RESEARCH_DECISION_INTERVAL_MS = 5 * CANDLE_INTERVAL_MS
FEATURE_SET = "common_candles_v1"


class ResearchDataError(ValueError):
    """Raised when historical data cannot support a time-safe calculation."""


def is_research_decision_time(timestamp_ms: int) -> bool:
    """Return whether a UTC timestamp is a fixed five-minute research slot."""

    return timestamp_ms >= 0 and timestamp_ms % RESEARCH_DECISION_INTERVAL_MS == 0


@dataclass(frozen=True)
class NormalizedCandle:
    """A confirmed one-minute OHLCV bar with its earliest usable time.

    ``available_at_ms`` prevents a replay from inspecting a bar before it was
    confirmed and delivered.  Historical imports may set it to the close time
    plus a fixed delivery delay; forward collection records the receive time.
    """

    venue: str
    symbol: str
    open_time_ms: int
    close_exclusive_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    received_at_ms: int
    available_at_ms: int
    availability_kind: str

    def __post_init__(self) -> None:
        if not self.venue or not self.symbol or not self.availability_kind:
            raise ResearchDataError("venue, symbol, and availability_kind are required")
        if self.open_time_ms < 0 or self.open_time_ms % CANDLE_INTERVAL_MS:
            raise ResearchDataError("open_time_ms must be aligned to one minute")
        if self.close_exclusive_ms != self.open_time_ms + CANDLE_INTERVAL_MS:
            raise ResearchDataError("candle must span exactly one minute")
        if self.received_at_ms < self.close_exclusive_ms:
            raise ResearchDataError("received_at_ms precedes candle confirmation")
        if self.available_at_ms < self.close_exclusive_ms:
            raise ResearchDataError("available_at_ms precedes candle confirmation")
        prices = (self.open, self.high, self.low, self.close)
        if not all(isfinite(value) and value > 0 for value in prices):
            raise ResearchDataError("OHLC values must be finite and positive")
        if self.high < max(self.open, self.close):
            raise ResearchDataError("high is below open or close")
        if self.low > min(self.open, self.close):
            raise ResearchDataError("low is above open or close")
        if not isfinite(self.volume) or self.volume < 0:
            raise ResearchDataError("volume must be finite and non-negative")


@dataclass(frozen=True)
class CommonCandleFeatures:
    """The venue-neutral one-minute feature set supplied to research agents."""

    feature_set: str
    as_of_ms: int
    return_1m: float
    return_5m: float
    return_15m: float
    return_60m: float
    realized_vol_5m: float
    realized_vol_30m: float
    atr_pct: float
    volume_zscore: float

    def to_prompt_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


def validate_contiguous_candles(candles: list[NormalizedCandle]) -> None:
    """Reject duplicates, mixed markets, and missing one-minute intervals."""

    if not candles:
        raise ResearchDataError("at least one candle is required")
    venue = candles[0].venue
    symbol = candles[0].symbol
    previous_open_time_ms: int | None = None
    for candle in candles:
        if candle.venue != venue or candle.symbol != symbol:
            raise ResearchDataError("candles must use one venue and symbol")
        if previous_open_time_ms is not None:
            expected = previous_open_time_ms + CANDLE_INTERVAL_MS
            if candle.open_time_ms != expected:
                if candle.open_time_ms <= previous_open_time_ms:
                    raise ResearchDataError("candles are not strictly ordered")
                raise ResearchDataError("candle gap detected")
        previous_open_time_ms = candle.open_time_ms


def _returns(closes: list[float], minutes: int) -> float:
    previous = closes[-1 - minutes]
    if previous <= 0:
        raise ResearchDataError("candle close must be positive")
    return closes[-1] / previous - 1.0


def _realized_volatility(closes: list[float], minutes: int) -> float:
    window = closes[-(minutes + 1) :]
    returns = [window[index] / window[index - 1] - 1.0 for index in range(1, len(window))]
    return pstdev(returns) if len(returns) > 1 else 0.0


def _atr_pct(candles: list[NormalizedCandle]) -> float:
    true_ranges: list[float] = []
    for index in range(len(candles) - 14, len(candles)):
        candle = candles[index]
        previous_close = candles[index - 1].close
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    return sum(true_ranges) / len(true_ranges) / candles[-1].close * 100.0


def build_common_candle_features(
    *,
    candles: list[NormalizedCandle],
    decision_time_ms: int,
) -> CommonCandleFeatures:
    """Build ``common_candles_v1`` without leaking unavailable future bars."""

    if decision_time_ms < 0:
        raise ResearchDataError("decision_time_ms must be non-negative")
    available = [candle for candle in candles if candle.available_at_ms <= decision_time_ms]
    if len(available) < 61:
        raise ResearchDataError("at least 61 available candles are required")
    window = available[-61:]
    validate_contiguous_candles(window)

    closes = [candle.close for candle in window]
    baseline_volumes = [candle.volume for candle in window[:-1]]
    volume_mean = sum(baseline_volumes) / len(baseline_volumes)
    volume_std = pstdev(baseline_volumes)
    volume_zscore = (window[-1].volume - volume_mean) / volume_std if volume_std else 0.0

    return CommonCandleFeatures(
        feature_set=FEATURE_SET,
        as_of_ms=window[-1].close_exclusive_ms,
        return_1m=_returns(closes, 1),
        return_5m=_returns(closes, 5),
        return_15m=_returns(closes, 15),
        return_60m=_returns(closes, 60),
        realized_vol_5m=_realized_volatility(closes, 5),
        realized_vol_30m=_realized_volatility(closes, 30),
        atr_pct=_atr_pct(window),
        volume_zscore=volume_zscore,
    )


def normalize_hyperliquid_candles(
    *,
    raw_candles: list[dict[str, Any]],
    coin: str,
    received_at_ms: int,
    delivery_delay_ms: int = 0,
) -> list[NormalizedCandle]:
    """Convert a public Hyperliquid snapshot into confirmed one-minute bars.

    ``T`` is the inclusive end timestamp in the Hyperliquid response.  The
    in-progress final bar is deliberately omitted; historical replays use the
    configured close-plus-delay time rather than the much later download time.
    """

    if received_at_ms < 0 or delivery_delay_ms < 0:
        raise ResearchDataError("receive and delivery times must be non-negative")
    normalized: list[NormalizedCandle] = []
    for raw in raw_candles:
        try:
            open_time_ms = int(raw["t"])
            close_exclusive_ms = int(raw["T"]) + 1
        except (KeyError, TypeError, ValueError) as error:
            raise ResearchDataError("Hyperliquid candle is missing t or T") from error
        if close_exclusive_ms > received_at_ms:
            continue
        try:
            normalized.append(
                NormalizedCandle(
                    venue="hyperliquid_mainnet_public",
                    symbol=coin,
                    open_time_ms=open_time_ms,
                    close_exclusive_ms=close_exclusive_ms,
                    open=float(raw["o"]),
                    high=float(raw["h"]),
                    low=float(raw["l"]),
                    close=float(raw["c"]),
                    volume=float(raw["v"]),
                    received_at_ms=received_at_ms,
                    available_at_ms=close_exclusive_ms + delivery_delay_ms,
                    availability_kind="historical_close_plus_delay",
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ResearchDataError("Hyperliquid candle has invalid OHLCV fields") from error
    normalized.sort(key=lambda candle: candle.open_time_ms)
    if normalized:
        validate_contiguous_candles(normalized)
    return normalized


def write_normalized_candles_jsonl(path: Path, candles: list[NormalizedCandle]) -> None:
    """Atomically persist a validated candle run outside Git-managed artifacts."""

    validate_contiguous_candles(candles)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for candle in candles:
            handle.write(json.dumps(asdict(candle), sort_keys=True, separators=(",", ":")))
            handle.write("\n")
    temporary.replace(path)


def read_normalized_candles_jsonl(path: Path) -> list[NormalizedCandle]:
    """Load the exact normalized rows and re-check their time ordering."""

    candles: list[NormalizedCandle] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise TypeError("row is not an object")
                candles.append(NormalizedCandle(**payload))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ResearchDataError(f"invalid normalized candle at line {line_number}") from error
    if not candles:
        return []
    validate_contiguous_candles(candles)
    return candles
