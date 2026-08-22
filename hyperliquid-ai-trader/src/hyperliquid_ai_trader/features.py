"""Compression of REST market data into a small trader instrument panel."""

from __future__ import annotations

from statistics import pstdev

from .models import BookLevel, Candle, MarketFeatures


class FeatureError(ValueError):
    """Raised when a reliable feature snapshot cannot be built."""


def _return(closes: list[float], minutes: int) -> float:
    previous = closes[-1 - minutes]
    if previous <= 0:
        raise FeatureError("candle close must be positive")
    return closes[-1] / previous - 1.0


def _realized_vol(closes: list[float], minutes: int) -> float:
    window = closes[-(minutes + 1) :]
    returns = [window[index] / window[index - 1] - 1.0 for index in range(1, len(window))]
    return pstdev(returns) if len(returns) > 1 else 0.0


def build_market_features(
    *,
    candles: list[Candle],
    bids: list[BookLevel],
    asks: list[BookLevel],
    mark: float,
    oracle: float,
    funding: float,
    open_interest: float,
) -> MarketFeatures:
    if len(candles) < 61:
        raise FeatureError("at least 61 candles are required")
    if not bids or not asks:
        raise FeatureError("order book must contain both sides")

    best_bid = max(level.price for level in bids)
    best_ask = min(level.price for level in asks)
    if best_bid <= 0 or best_ask <= best_bid:
        raise FeatureError("order book is empty or crossed")

    closes = [candle.close for candle in candles]
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 10_000.0

    bid_size = sum(max(level.size, 0.0) for level in bids[:10])
    ask_size = sum(max(level.size, 0.0) for level in asks[:10])
    total_size = bid_size + ask_size
    if total_size <= 0:
        raise FeatureError("order book sizes must be positive")
    book_imbalance = (bid_size - ask_size) / total_size

    true_ranges: list[float] = []
    for index in range(max(1, len(candles) - 14), len(candles)):
        candle = candles[index]
        previous_close = candles[index - 1].close
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    atr = sum(true_ranges) / len(true_ranges)
    atr_pct = atr / mark * 100.0 if mark > 0 else 0.0

    baseline_volumes = [candle.volume for candle in candles[-61:-1]]
    volume_mean = sum(baseline_volumes) / len(baseline_volumes)
    volume_std = pstdev(baseline_volumes)
    volume_zscore = (candles[-1].volume - volume_mean) / volume_std if volume_std > 0 else 0.0

    return MarketFeatures(
        mid=mid,
        mark=mark,
        oracle=oracle,
        funding=funding,
        open_interest=open_interest,
        return_1m=_return(closes, 1),
        return_5m=_return(closes, 5),
        return_15m=_return(closes, 15),
        return_60m=_return(closes, 60),
        realized_vol_5m=_realized_vol(closes, 5),
        realized_vol_30m=_realized_vol(closes, 30),
        atr_pct=atr_pct,
        spread_bps=spread_bps,
        book_imbalance=book_imbalance,
        volume_zscore=volume_zscore,
    )
