"""Time-safe one-minute candle normalization and common OHLCV features."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import pstdev


CANDLE_INTERVAL_MS = 60_000
FEATURE_SET = "common_candles_v1"


class ResearchDataError(ValueError):
    """Raised when historical data cannot support a time-safe calculation."""


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
