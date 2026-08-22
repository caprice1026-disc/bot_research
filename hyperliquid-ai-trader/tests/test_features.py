from __future__ import annotations

import pytest

from hyperliquid_ai_trader.features import (
    FeatureError,
    build_market_features,
    estimate_mfe_mae_pct,
)
from hyperliquid_ai_trader.models import BookLevel, Candle


def _candles() -> list[Candle]:
    candles: list[Candle] = []
    for index in range(61):
        close = 100.0 + index
        volume = 90.0 if index % 2 == 0 else 110.0
        if index == 60:
            volume = 130.0
        candles.append(
            Candle(
                timestamp_ms=index * 60_000,
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=volume,
            )
        )
    return candles


def test_build_market_features_compresses_candles_and_book() -> None:
    features = build_market_features(
        candles=_candles(),
        bids=[BookLevel(price=159.9, size=3.0), BookLevel(price=159.8, size=1.0)],
        asks=[BookLevel(price=160.1, size=1.0), BookLevel(price=160.2, size=1.0)],
        mark=160.0,
        oracle=160.2,
        funding=0.00001,
        open_interest=25_000.0,
    )

    assert features.return_1m == pytest.approx(160 / 159 - 1)
    assert features.return_5m == pytest.approx(160 / 155 - 1)
    assert features.return_15m == pytest.approx(160 / 145 - 1)
    assert features.return_60m == pytest.approx(160 / 100 - 1)
    assert features.atr_pct == pytest.approx(1.25)
    assert features.volume_zscore == pytest.approx(3.0)
    assert features.mid == pytest.approx(160.0)
    assert features.spread_bps == pytest.approx(12.5)
    assert features.book_imbalance == pytest.approx(1 / 3)
    assert features.mark == 160.0
    assert features.oracle == 160.2


def test_build_market_features_requires_sixty_minutes_of_history() -> None:
    with pytest.raises(FeatureError, match="61 candles"):
        build_market_features(
            candles=_candles()[:60],
            bids=[BookLevel(price=99.0, size=1.0)],
            asks=[BookLevel(price=101.0, size=1.0)],
            mark=100.0,
            oracle=100.0,
            funding=0.0,
            open_interest=1.0,
        )


def test_build_market_features_rejects_crossed_or_empty_book() -> None:
    with pytest.raises(FeatureError, match="order book"):
        build_market_features(
            candles=_candles(),
            bids=[BookLevel(price=161.0, size=1.0)],
            asks=[BookLevel(price=160.0, size=1.0)],
            mark=160.0,
            oracle=160.0,
            funding=0.0,
            open_interest=1.0,
        )


def test_mfe_mae_estimate_respects_long_and_short_direction() -> None:
    candles = [
        Candle(1_000, 100, 102, 99, 101, 1),
        Candle(2_000, 101, 105, 98, 102, 1),
    ]

    assert estimate_mfe_mae_pct(
        candles=candles,
        side="long",
        entry_price=100.0,
        start_ms=1_000,
        end_ms=3_000,
    ) == pytest.approx((5.0, 2.0))
    assert estimate_mfe_mae_pct(
        candles=candles,
        side="short",
        entry_price=100.0,
        start_ms=1_000,
        end_ms=3_000,
    ) == pytest.approx((2.0, 5.0))
