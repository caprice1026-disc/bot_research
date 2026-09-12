"""Deterministic, stratified point selection for offline model evaluation."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from random import Random
from statistics import median

from .data import (
    CommonCandleFeatures,
    FEATURE_SET,
    NormalizedCandle,
    RESEARCH_DECISION_INTERVAL_MS,
    build_common_candle_features,
    is_research_decision_time,
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
        if not is_research_decision_time(self.decision_time_ms):
            raise PointSelectionError(
                f"decision time must align to {RESEARCH_DECISION_INTERVAL_MS}ms"
            )
        if self.features.feature_set != FEATURE_SET:
            raise PointSelectionError("candidate uses an unsupported feature set")


@dataclass(frozen=True)
class PointSelection:
    points: tuple[PointCandidate, ...]
    stratum_counts: dict[str, int]
    candidate_stratum_counts: dict[str, int]
    realized_vol_30m_median: float


def candidate_set_sha256(candidates: list[PointCandidate]) -> str:
    """Hash the complete ordered candidate population, not only a selection prefix."""

    try:
        payload = json.dumps(
            [
                {
                    "decision_time_ms": candidate.decision_time_ms,
                    "features": candidate.features.to_prompt_dict(),
                }
                for candidate in candidates
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except ValueError as error:
        raise PointSelectionError("candidate features must be finite JSON values") from error
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_point_selection_jsonl(path: Path, selection: PointSelection) -> None:
    """Atomically persist selected, time-safe feature inputs for later requests."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for point in selection.points:
            handle.write(
                json.dumps(
                    {
                        "decision_time_ms": point.decision_time_ms,
                        "features": point.features.to_prompt_dict(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
            handle.write("\n")
    temporary.replace(path)


def read_point_selection_jsonl(path: Path) -> list[PointCandidate]:
    """Load a selected point artifact and revalidate its time-safe schema."""

    points: list[PointCandidate] = []
    expected_feature_keys = set(CommonCandleFeatures.__dataclass_fields__)
    feature_float_keys = expected_feature_keys - {"feature_set", "as_of_ms"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PointSelectionError(f"cannot read point selection: {path}") from error
    for line_number, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict) or set(payload) != {"decision_time_ms", "features"}:
                raise TypeError("point row keys are invalid")
            decision_time_ms = payload["decision_time_ms"]
            feature_values = payload["features"]
            if isinstance(decision_time_ms, bool) or not isinstance(decision_time_ms, int):
                raise TypeError("decision_time_ms must be an integer")
            if not isinstance(feature_values, dict) or set(feature_values) != expected_feature_keys:
                raise TypeError("feature keys are invalid")
            if not isinstance(feature_values["feature_set"], str):
                raise TypeError("feature_set must be a string")
            if isinstance(feature_values["as_of_ms"], bool) or not isinstance(feature_values["as_of_ms"], int):
                raise TypeError("as_of_ms must be an integer")
            if any(
                isinstance(feature_values[key], bool)
                or not isinstance(feature_values[key], (int, float))
                for key in feature_float_keys
            ):
                raise TypeError("feature values must be numbers")
            features = CommonCandleFeatures(**feature_values)
            points.append(PointCandidate(decision_time_ms=decision_time_ms, features=features))
        except (TypeError, ValueError, json.JSONDecodeError, PointSelectionError) as error:
            raise PointSelectionError(f"invalid point selection at line {line_number}") from error
    decision_times = [point.decision_time_ms for point in points]
    if not points:
        raise PointSelectionError("point selection is empty")
    if len(set(decision_times)) != len(decision_times):
        raise PointSelectionError("point selection decision times must be unique")
    return points


def build_point_candidates(candles: list[NormalizedCandle]) -> list[PointCandidate]:
    """Build candidates only once each point has 61 confirmed one-minute bars."""

    if not candles:
        return []
    validate_contiguous_candles(candles)
    candidates: list[PointCandidate] = []
    for candle in candles[60:]:
        decision_time_ms = candle.close_exclusive_ms
        if not is_research_decision_time(decision_time_ms):
            continue
        if sum(candidate.available_at_ms <= decision_time_ms for candidate in candles) < 61:
            continue
        candidates.append(
            PointCandidate(
                decision_time_ms=decision_time_ms,
                features=build_common_candle_features(
                    candles=candles,
                    decision_time_ms=decision_time_ms,
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
    candidate_counts = Counter(
        _stratum(candidate, median_volatility=median_volatility)
        for candidate in candidates
    )

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
    return PointSelection(
        points=selected,
        stratum_counts=dict(sorted(counts.items())),
        candidate_stratum_counts=dict(sorted(candidate_counts.items())),
        realized_vol_30m_median=median_volatility,
    )
