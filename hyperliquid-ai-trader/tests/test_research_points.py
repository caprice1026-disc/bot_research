from __future__ import annotations

import pytest

from hyperliquid_ai_trader.research.data import CommonCandleFeatures
from hyperliquid_ai_trader.research.points import (
    PointCandidate,
    PointSelectionError,
    build_point_candidates,
    select_research_points,
)
from hyperliquid_ai_trader.research.data import CANDLE_INTERVAL_MS, NormalizedCandle


def _candidate(index: int) -> PointCandidate:
    return PointCandidate(
        decision_time_ms=index * 60_000,
        features=CommonCandleFeatures(
            feature_set="common_candles_v1",
            as_of_ms=index * 60_000,
            return_1m=0.0,
            return_5m=0.01 if index % 2 else -0.01,
            return_15m=0.0,
            return_60m=0.0,
            realized_vol_5m=0.01,
            realized_vol_30m=0.01 if index % 4 < 2 else 0.03,
            atr_pct=0.1,
            volume_zscore=1.0 if index % 4 in {1, 2} else -1.0,
        ),
    )


def test_seeded_point_selection_is_unique_and_pilot_is_prefix_of_full_selection() -> None:
    candidates = [_candidate(index) for index in range(16)]

    pilot = select_research_points(candidates, count=5, seed=42)
    full = select_research_points(candidates, count=12, seed=42)

    pilot_ids = [point.decision_time_ms for point in pilot.points]
    full_ids = [point.decision_time_ms for point in full.points]
    assert pilot_ids == full_ids[:5]
    assert len(set(full_ids)) == 12
    assert sum(full.stratum_counts.values()) == 12


def test_point_selection_rejects_invalid_requested_count_and_insufficient_candidates() -> None:
    candidates = [_candidate(index) for index in range(2)]

    with pytest.raises(PointSelectionError, match="at most 500"):
        select_research_points(candidates, count=501, seed=42)
    with pytest.raises(PointSelectionError, match="only 2"):
        select_research_points(candidates, count=3, seed=42)


def test_point_candidates_start_after_61_confirmed_one_minute_candles() -> None:
    candles = [
        NormalizedCandle(
            venue="hyperliquid_mainnet_public",
            symbol="BTC",
            open_time_ms=index * CANDLE_INTERVAL_MS,
            close_exclusive_ms=(index + 1) * CANDLE_INTERVAL_MS,
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=100.0 + index,
            received_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            available_at_ms=(index + 1) * CANDLE_INTERVAL_MS,
            availability_kind="fixture",
        )
        for index in range(65)
    ]

    candidates = build_point_candidates(candles)

    assert len(candidates) == 5
    assert candidates[0].decision_time_ms == 61 * CANDLE_INTERVAL_MS
    assert candidates[0].features.as_of_ms == 61 * CANDLE_INTERVAL_MS
