"""Probability calculations for temperature buckets."""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, isfinite, sqrt
from collections.abc import Mapping, Sequence


@dataclass(frozen=True)
class Bucket:
    lower: float | None = None
    upper: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    def __post_init__(self) -> None:
        if self.lower is None and self.upper is None:
            raise ValueError("bucket must have at least one bound")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("bucket lower must not exceed upper")


def _contains(value: float, bucket: Bucket) -> bool:
    lower_ok = (
        True
        if bucket.lower is None
        else value >= bucket.lower
        if bucket.lower_inclusive
        else value > bucket.lower
    )
    upper_ok = (
        True
        if bucket.upper is None
        else value <= bucket.upper
        if bucket.upper_inclusive
        else value < bucket.upper
    )
    return lower_ok and upper_ok


def ensemble_bucket_probability(values: Sequence[float], bucket: Bucket) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if any(not isfinite(value) for value in values):
        raise ValueError("values must be finite")
    return sum(_contains(value, bucket) for value in values) / len(values)


def _normal_cdf(value: float, mean: float, standard_deviation: float) -> float:
    return 0.5 * (1.0 + erf((value - mean) / (standard_deviation * sqrt(2.0))))


def gaussian_bucket_probability(
    mean: float,
    standard_deviation: float,
    bucket: Bucket,
) -> float:
    if not isfinite(mean):
        raise ValueError("mean must be finite")
    if not isfinite(standard_deviation) or standard_deviation <= 0:
        raise ValueError("standard_deviation must be positive")
    lower_probability = (
        0.0
        if bucket.lower is None
        else _normal_cdf(bucket.lower, mean, standard_deviation)
    )
    upper_probability = (
        1.0
        if bucket.upper is None
        else _normal_cdf(bucket.upper, mean, standard_deviation)
    )
    return max(0.0, min(1.0, upper_probability - lower_probability))


def normalize_distribution(probabilities: Mapping[str, float]) -> dict[str, float]:
    if not probabilities:
        raise ValueError("probabilities must not be empty")
    if any(value < 0 or not isfinite(value) for value in probabilities.values()):
        raise ValueError("probabilities must be finite and non-negative")
    total = sum(probabilities.values())
    if total <= 0:
        raise ValueError("probabilities must have a positive total")
    return {key: value / total for key, value in probabilities.items()}
