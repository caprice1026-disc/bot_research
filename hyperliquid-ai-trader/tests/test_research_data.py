from __future__ import annotations

import pytest

from hyperliquid_ai_trader.research.data import (
    CANDLE_INTERVAL_MS,
    ResearchDataError,
    NormalizedCandle,
    build_common_candle_features,
    validate_contiguous_candles,
)


def _candles(*, count: int = 62, available_delay_ms: int = 0) -> list[NormalizedCandle]:
    candles: list[NormalizedCandle] = []
    for index in range(count):
        close = 100.0 + index
        open_time_ms = index * CANDLE_INTERVAL_MS
        close_exclusive_ms = open_time_ms + CANDLE_INTERVAL_MS
        candles.append(
            NormalizedCandle(
                venue="binance",
                symbol="BTCUSDT",
                open_time_ms=open_time_ms,
                close_exclusive_ms=close_exclusive_ms,
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=100.0 + (index % 3),
                received_at_ms=close_exclusive_ms + available_delay_ms,
                available_at_ms=close_exclusive_ms + available_delay_ms,
                availability_kind="confirmed_history",
            )
        )
    return candles


def test_common_features_use_only_candles_available_at_decision_time() -> None:
    candles = _candles()
    decision_time_ms = candles[60].close_exclusive_ms

    first = build_common_candle_features(
        candles=candles,
        decision_time_ms=decision_time_ms,
    )
    changed_future = [*candles]
    future = changed_future[61]
    changed_future[61] = NormalizedCandle(
        venue=future.venue,
        symbol=future.symbol,
        open_time_ms=future.open_time_ms,
        close_exclusive_ms=future.close_exclusive_ms,
        open=future.open,
        high=9_999.0,
        low=1.0,
        close=9_999.0,
        volume=999_999.0,
        received_at_ms=future.received_at_ms,
        available_at_ms=future.available_at_ms,
        availability_kind=future.availability_kind,
    )
    second = build_common_candle_features(
        candles=changed_future,
        decision_time_ms=decision_time_ms,
    )

    assert first == second
    assert first.as_of_ms == decision_time_ms
    assert first.return_5m == pytest.approx(160.0 / 155.0 - 1.0)
    assert first.atr_pct == pytest.approx(1.25)
    assert first.to_prompt_dict()["feature_set"] == "common_candles_v1"


def test_common_features_reject_candle_not_available_at_decision_time() -> None:
    candles = _candles(count=61)
    delayed = candles[-1]
    candles[-1] = NormalizedCandle(
        venue=delayed.venue,
        symbol=delayed.symbol,
        open_time_ms=delayed.open_time_ms,
        close_exclusive_ms=delayed.close_exclusive_ms,
        open=delayed.open,
        high=delayed.high,
        low=delayed.low,
        close=delayed.close,
        volume=delayed.volume,
        received_at_ms=delayed.received_at_ms + CANDLE_INTERVAL_MS,
        available_at_ms=delayed.available_at_ms + CANDLE_INTERVAL_MS,
        availability_kind=delayed.availability_kind,
    )

    with pytest.raises(ResearchDataError, match="61 available"):
        build_common_candle_features(
            candles=candles,
            decision_time_ms=delayed.close_exclusive_ms,
        )


def test_contiguous_validation_rejects_a_gap_and_invalid_ohlc() -> None:
    candles = _candles(count=3)
    gap = candles[1]
    candles[1] = NormalizedCandle(
        venue=gap.venue,
        symbol=gap.symbol,
        open_time_ms=gap.open_time_ms + CANDLE_INTERVAL_MS,
        close_exclusive_ms=gap.close_exclusive_ms + CANDLE_INTERVAL_MS,
        open=gap.open,
        high=gap.high,
        low=gap.low,
        close=gap.close,
        volume=gap.volume,
        received_at_ms=gap.received_at_ms + CANDLE_INTERVAL_MS,
        available_at_ms=gap.available_at_ms + CANDLE_INTERVAL_MS,
        availability_kind=gap.availability_kind,
    )

    with pytest.raises(ResearchDataError, match="gap"):
        validate_contiguous_candles(candles)

    with pytest.raises(ResearchDataError, match="high"):
        NormalizedCandle(
            venue="binance",
            symbol="BTCUSDT",
            open_time_ms=0,
            close_exclusive_ms=CANDLE_INTERVAL_MS,
            open=100.0,
            high=99.0,
            low=98.0,
            close=100.0,
            volume=1.0,
            received_at_ms=CANDLE_INTERVAL_MS,
            available_at_ms=CANDLE_INTERVAL_MS,
            availability_kind="confirmed_history",
        )
