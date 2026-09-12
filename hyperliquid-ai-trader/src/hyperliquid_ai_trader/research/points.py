"""Deterministic, stratified point selection for offline model evaluation."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
from random import Random
from statistics import median

from .data import (
    CommonCandleFeatures,
    FEATURE_SET,
    NormalizedCandle,
    build_common_candle_features,
    validate_contiguous_candles,
)


MAX_RESEARCH_POINTS = 500


class PointSelectionError(ValueError):
    """Raised when a reproducible point set cannot be selected."""


@dataclass(frozen=True)
class PointCandidate:
    decision_time_ms: int
    features: CommonCandleFeatures

    def __post_init__(self) -> None:
        if self.decision_time_ms < self.features.as_of_ms:
            raise PointSelectionError("decision time precedes available features")
        if self.features.feature_set != FEATURE_SET:
            raise PointSelectionError("candidate uses an unsupported feature set")


@dataclass(frozen=True)
class PointSelection:
    points: tuple[PointCandidate, ...]
    stratum_counts: dict[str, int]


def build_point_candidates(candles: list[NormalizedCandle]) -> list[PointCandidate]:
    """Build candidates only once each point has 61 confirmed one-minute bars."""

    if not candles:
        return []
    validate_contiguous_candles(candles)
    candidates: list[PointCandidate] = []
    for candle in candles[60:]:
        candidates.append(
            PointCandidate(
                decision_time_ms=candle.available_at_ms,
                features=build_common_candle_features(
                    candles=candles,
                    decision_time_ms=candle.available_at_ms,
                ),
            )
        )
    return candidates


def _stratum(point: PointCandidate, *, median_volatility: float) -> str:
    direction = "up" if point.features.return_5m > 0 else "down_or_flat"
    volatility = "high_vol" if point.features.realized_vol_30m >= median_volatility else "low_vol"
    volume = "high_volume" if point.features.volume_zscore >= 0 else "low_volume"
    return ":".join((direction, volatility, volume))


def _seed_for_stratum(seed: int, stratum: str) -> int:
    digest = hashlib.sha256(f"{seed}:{stratum}".encode("utf-8")).digest()
    return int.from_bytes(digest, "big")


def select_research_points(
    candidates: list[PointCandidate],
    *,
    count: int,
    seed: int,
) -> PointSelection:
    """Return a deterministic round-robin sample; smaller samples are prefixes.

    A given candidate population and seed create one balanced ranking.  Thus a
    50-point pilot is the prefix of the corresponding 500-point selection.
    """

    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_RESEARCH_POINTS:
        raise PointSelectionError(f"count must be between 1 and at most {MAX_RESEARCH_POINTS}")
    if len(candidates) < count:
        raise PointSelectionError(f"only {len(candidates)} candidates are available")
    decision_times = [candidate.decision_time_ms for candidate in candidates]
    if len(set(decision_times)) != len(decision_times):
        raise PointSelectionError("candidate decision times must be unique")

    median_volatility = median(candidate.features.realized_vol_30m for candidate in candidates)
    grouped: dict[str, list[PointCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(_stratum(candidate, median_volatility=median_volatility), []).append(candidate)

    queues: dict[str, deque[PointCandidate]] = {}
    for stratum, group in grouped.items():
        ordered = sorted(group, key=lambda candidate: candidate.decision_time_ms)
        Random(_seed_for_stratum(seed, stratum)).shuffle(ordered)
        queues[stratum] = deque(ordered)

    ranked: list[PointCandidate] = []
    while queues:
        for stratum in sorted(tuple(queues)):
            ranked.append(queues[stratum].popleft())
            if not queues[stratum]:
                del queues[stratum]

    selected = tuple(ranked[:count])
    counts = Counter(
        _stratum(candidate, median_volatility=median_volatility)
        for candidate in selected
    )
    return PointSelection(points=selected, stratum_counts=dict(sorted(counts.items())))
