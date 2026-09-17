"""Configuration and feature-derived decisions for conditional offline research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import hashlib
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..models import Side, TradeDecision
from .binance import BINANCE_USDM_VENUE, FUNDING_INTERVAL_MS, FundingDataError, FundingSeries
from .config import ResearchConfig, ResearchConfigError, load_research_config
from .costs import estimated_round_trip_cost_bps
from .data import (
    FEATURE_SET,
    RESEARCH_DECISION_INTERVAL_MS,
    CandleSeries,
    CommonCandleFeatures,
    NormalizedCandle,
    ResearchDataError,
    validate_candles_match_market,
)
from .simulator import SimulatedEpisode, SimulationError, simulate_episode_from_entry, with_funding


class ConditionalStudyError(ValueError):
    """Raised when a conditional study cannot be reproduced safely."""


@dataclass(frozen=True)
class PromotionPolicy:
    minimum_validation_trades: int
    minimum_side_trades: int
    positive_validation_month_fraction: Decimal
    max_drawdown_pct: Decimal
    both_directions_positive: bool
    no_drawdown_stop: bool


@dataclass(frozen=True)
class ConditionalStudyConfig:
    path: Path
    experiment_id: str
    base_config_path: Path
    base_config: ResearchConfig
    start_ms: int
    end_ms: int
    exploration_end_ms: int
    validation_end_ms: int
    confirmation_start_ms: int
    holds_ms: tuple[int, ...]
    exit_profiles: tuple[str, ...]
    quantiles: tuple[Decimal, ...]
    candidate_specs: tuple[str, ...]
    promotion_policy: PromotionPolicy
    raw: dict[str, Any]


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    hold_ms: int
    exit_profile: str
    threshold_quantile: Decimal | None
    cost_bps: Decimal

    def __post_init__(self) -> None:
        if self.name not in {"A", "B", "C", "D", "E"}:
            raise ConditionalStudyError("candidate name must be A through E")
        if self.hold_ms <= 0 or self.hold_ms % 60_000:
            raise ConditionalStudyError("candidate hold must be a positive whole minute")
        if self.exit_profile not in {"hold_only", "fixed_sl_tp"}:
            raise ConditionalStudyError("candidate exit profile is invalid")
        if self.name in {"A", "B"} and self.threshold_quantile is not None:
            raise ConditionalStudyError("candidate A/B do not use threshold quantiles")
        if self.name in {"C", "D", "E"} and (
            self.threshold_quantile is None
            or not Decimal("0") < self.threshold_quantile < Decimal("1")
        ):
            raise ConditionalStudyError("candidate C/D/E require a threshold quantile")
        if self.cost_bps <= 0 or not self.cost_bps.is_finite():
            raise ConditionalStudyError("candidate cost must be positive and finite")

    @property
    def candidate_id(self) -> str:
        payload = {
            "name": self.name,
            "hold_ms": self.hold_ms,
            "exit_profile": self.exit_profile,
            "threshold_quantile": _decimal_text(self.threshold_quantile)
            if self.threshold_quantile is not None
            else None,
            "cost_bps": _decimal_text(self.cost_bps),
        }
        return f"{self.name.lower()}-{_canonical_config_sha256(payload)[:12]}"


@dataclass(frozen=True)
class ConditionalLabel:
    """One independent outcome label for a study decision point."""

    decision_time_ms: int
    side: Side
    hold_ms: int
    exit_profile: str
    status: str
    features: CommonCandleFeatures | None
    episode: SimulatedEpisode | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ConditionalLabelFiles:
    features_path: Path
    labels_path: Path
    features_manifest_path: Path
    labels_manifest_path: Path
    feature_count: int
    label_count: int
    status_counts: dict[str, int]
    quality: str


def derive_regime_features(
    features: Mapping[str, object], execution: object
) -> dict[str, Decimal | str | None]:
    """Derive only contemporaneous, cost-normalized market-state quantities."""

    try:
        return_15m = _feature_decimal(features, "return_15m")
        return_60m = _feature_decimal(features, "return_60m")
        vol_5m = _feature_decimal(features, "realized_vol_5m")
        vol_30m = _feature_decimal(features, "realized_vol_30m")
        atr_pct = _feature_decimal(features, "atr_pct")
        volume_zscore = _feature_decimal(features, "volume_zscore")
    except (KeyError, InvalidOperation, ValueError) as error:
        raise ConditionalStudyError("features are missing a finite regime input") from error
    return _derive_regime_features_with_cost(
        return_15m=return_15m,
        return_60m=return_60m,
        vol_5m=vol_5m,
        vol_30m=vol_30m,
        atr_pct=atr_pct,
        volume_zscore=volume_zscore,
        cost_bps=estimated_round_trip_cost_bps(execution),
    )


def _derive_regime_features_with_cost(
    *,
    return_15m: Decimal,
    return_60m: Decimal,
    vol_5m: Decimal,
    vol_30m: Decimal,
    atr_pct: Decimal,
    volume_zscore: Decimal,
    cost_bps: Decimal,
) -> dict[str, Decimal | str | None]:
    atr_bps = atr_pct * Decimal("100")
    vol_30m_bps = vol_30m * Decimal("10000")
    if return_15m > 0 and return_60m > 0:
        alignment = "up"
    elif return_15m < 0 and return_60m < 0:
        alignment = "down"
    elif return_15m == 0 or return_60m == 0:
        alignment = "flat"
    else:
        alignment = "mixed"
    return {
        "alignment": alignment,
        "cost_bps": cost_bps,
        "atr_bps": atr_bps,
        "vol_30m_bps": vol_30m_bps,
        "atr_to_cost": atr_bps / cost_bps if cost_bps > 0 else None,
        "vol_to_cost": vol_30m_bps / cost_bps if cost_bps > 0 else None,
        "vol_expansion_ratio": vol_5m / vol_30m if vol_30m != 0 else None,
        "volume_zscore": volume_zscore,
    }


def fit_regime_thresholds(
    training_rows: list[Mapping[str, object]], quantiles: tuple[Decimal, ...]
) -> dict[str, dict[Decimal, Decimal]]:
    """Fit deterministic equal-frequency boundaries from training features only."""

    if not training_rows:
        raise ConditionalStudyError("regime thresholds require training rows")
    if not quantiles or any(not Decimal("0") < value < Decimal("1") for value in quantiles):
        raise ConditionalStudyError("regime quantiles must be between zero and one")
    columns = ("atr_to_cost", "vol_to_cost", "volume_zscore", "vol_expansion_ratio")
    values: dict[str, list[Decimal]] = {column: [] for column in columns}
    for row in training_rows:
        for column in columns:
            value = row.get(column)
            if isinstance(value, Decimal):
                values[column].append(value)
    thresholds: dict[str, dict[Decimal, Decimal]] = {}
    for column, samples in values.items():
        if not samples:
            continue
        samples.sort()
        thresholds[column] = {
            quantile: samples[min(len(samples) - 1, int((len(samples) - 1) * float(quantile)))]
            for quantile in quantiles
        }
    return thresholds


def candidate_decision(
    candidate: CandidateSpec,
    features: Mapping[str, object],
    thresholds: Mapping[str, Mapping[Decimal, Decimal]],
) -> TradeDecision:
    """Turn one fixed candidate rule into a decision, never inspecting labels."""

    return_15m = _feature_decimal(features, "return_15m")
    regime = _derive_regime_features_with_cost(
        return_15m=return_15m,
        return_60m=_feature_decimal(features, "return_60m"),
        vol_5m=_feature_decimal(features, "realized_vol_5m"),
        vol_30m=_feature_decimal(features, "realized_vol_30m"),
        atr_pct=_feature_decimal(features, "atr_pct"),
        volume_zscore=_feature_decimal(features, "volume_zscore"),
        cost_bps=candidate.cost_bps,
    )
    side: Side | None
    if candidate.name == "A":
        side = Side.LONG if return_15m > 0 else Side.SHORT if return_15m < 0 else None
    else:
        alignment = regime["alignment"]
        side = Side.LONG if alignment == "up" else Side.SHORT if alignment == "down" else None
        if side is not None and candidate.name in {"C", "D"}:
            assert candidate.threshold_quantile is not None
            if not _passes_threshold(regime, thresholds, "atr_to_cost", candidate.threshold_quantile) or not _passes_threshold(
                regime, thresholds, "vol_to_cost", candidate.threshold_quantile
            ):
                side = None
        if side is not None and candidate.name == "D":
            assert candidate.threshold_quantile is not None
            if not _passes_threshold(regime, thresholds, "volume_zscore", candidate.threshold_quantile):
                side = None
        if side is not None and candidate.name == "E":
            assert candidate.threshold_quantile is not None
            if not _passes_threshold(
                regime, thresholds, "vol_expansion_ratio", candidate.threshold_quantile
            ):
                side = None
    return TradeDecision(
        side=side or Side.LONG,
        stop_loss_pct=Decimal("0.30"),
        take_profit_pct=Decimal("0.60"),
        confidence=Decimal("0.5"),
        thesis=f"conditional candidate {candidate.name}",
        would_abstain=side is None,
        abstain_reason="candidate conditions not met" if side is None else None,
    )


def _feature_decimal(features: Mapping[str, object], name: str) -> Decimal:
    value = Decimal(str(features[name]))
    if not value.is_finite():
        raise ConditionalStudyError(f"feature {name} must be finite")
    return value


def _passes_threshold(
    regime: Mapping[str, Decimal | str | None],
    thresholds: Mapping[str, Mapping[Decimal, Decimal]],
    name: str,
    quantile: Decimal,
) -> bool:
    value = regime.get(name)
    threshold = thresholds.get(name, {}).get(quantile)
    return isinstance(value, Decimal) and threshold is not None and value >= threshold


def validate_study_inputs(
    *,
    candles: list[NormalizedCandle],
    funding: FundingSeries | None,
    study_config: ConditionalStudyConfig,
) -> dict[str, object]:
    """Validate exact input identity and report boundary coverage without inventing data."""

    if not candles:
        raise ConditionalStudyError("at least one candle is required")
    try:
        validate_candles_match_market(
            candles,
            venue=study_config.base_config.market_venue,
            symbol=study_config.base_config.symbol,
        )
        CandleSeries(candles)
    except ResearchDataError as error:
        raise ConditionalStudyError("candle input is invalid for the study") from error

    expected_funding_slots = 0
    if study_config.base_config.market_venue == BINANCE_USDM_VENUE:
        if funding is None:
            raise ConditionalStudyError("Binance USD-M study requires funding data")
        expected_slots = range(study_config.start_ms, study_config.end_ms, FUNDING_INTERVAL_MS)
        event_slots = {
            event.timestamp_ms // FUNDING_INTERVAL_MS * FUNDING_INTERVAL_MS
            for event in funding.events
        }
        missing_slots = [slot for slot in expected_slots if slot not in event_slots]
        expected_funding_slots = len(range(study_config.start_ms, study_config.end_ms, FUNDING_INTERVAL_MS))
        if missing_slots:
            raise ConditionalStudyError(
                f"funding input is missing {len(missing_slots)} scheduled slots"
            )

    return {
        "requested_start_ms": study_config.start_ms,
        "requested_end_exclusive_ms": study_config.end_ms,
        "observed_start_ms": candles[0].open_time_ms,
        "observed_end_exclusive_ms": candles[-1].close_exclusive_ms,
        "candle_count": len(candles),
        "history_before_requested_minutes": max(
            0, (study_config.start_ms - candles[0].open_time_ms) // 60_000
        ),
        "tail_after_requested_minutes": max(
            0, (candles[-1].close_exclusive_ms - study_config.end_ms) // 60_000
        ),
        "funding_event_count": len(funding.events) if funding is not None else 0,
        "expected_funding_slots": expected_funding_slots,
    }


def _mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConditionalStudyError(f"{name} must be an object")
    return value


def _string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConditionalStudyError(f"{name} must be a non-empty string")
    return value


def _decimal(value: Any, *, name: str) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ConditionalStudyError(f"{name} must be a decimal") from error
    if not decimal.is_finite():
        raise ConditionalStudyError(f"{name} must be finite")
    return decimal


def _timestamp(value: Any, *, name: str) -> int:
    text = _string(value, name=name)
    if not text.endswith("Z"):
        raise ConditionalStudyError(f"{name} must use a UTC Z timestamp")
    try:
        moment = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as error:
        raise ConditionalStudyError(f"{name} is not an ISO timestamp") from error
    if moment.tzinfo != timezone.utc:
        raise ConditionalStudyError(f"{name} must be UTC")
    return int(moment.timestamp() * 1_000)


def _positive_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConditionalStudyError(f"{name} must be a positive integer")
    return value


def _bool(value: Any, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConditionalStudyError(f"{name} must be a boolean")
    return value


def load_conditional_study_config(path: Path) -> ConditionalStudyConfig:
    """Load a public, time-bounded study without modifying the base config."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConditionalStudyError(f"cannot read conditional study config: {path}") from error
    root = _mapping(raw, name="conditional study config")
    if root.get("schema_version") != 1:
        raise ConditionalStudyError("schema_version must be 1")
    if root.get("decision_interval_ms") != RESEARCH_DECISION_INTERVAL_MS:
        raise ConditionalStudyError("decision_interval_ms must be 300000")
    if root.get("feature_set") != FEATURE_SET:
        raise ConditionalStudyError(f"feature_set must be {FEATURE_SET}")

    base_config_path = (path.parent / _string(root.get("base_config"), name="base_config")).resolve()
    try:
        base_config = load_research_config(base_config_path)
    except ResearchConfigError as error:
        raise ConditionalStudyError("base_config is invalid") from error
    if base_config.feature_set != FEATURE_SET:
        raise ConditionalStudyError("base_config feature_set does not match")
    if estimated_round_trip_cost_bps(base_config.execution) <= 0:
        raise ConditionalStudyError("base_config requires positive round-trip cost")

    period = _mapping(root.get("period"), name="period")
    start_ms = _timestamp(period.get("start_inclusive"), name="period.start_inclusive")
    end_ms = _timestamp(period.get("end_exclusive"), name="period.end_exclusive")
    if start_ms >= end_ms or start_ms % RESEARCH_DECISION_INTERVAL_MS or end_ms % RESEARCH_DECISION_INTERVAL_MS:
        raise ConditionalStudyError("period must be increasing and align to five-minute slots")

    holds = root.get("holds_ms")
    if not isinstance(holds, list) or not holds:
        raise ConditionalStudyError("holds_ms must be a non-empty list")
    holds_ms = tuple(_positive_int(value, name="holds_ms") for value in holds)
    if len(set(holds_ms)) != len(holds_ms) or any(value % 60_000 for value in holds_ms):
        raise ConditionalStudyError("holds_ms must be unique whole minutes")

    exit_profiles = root.get("exit_profiles")
    if not isinstance(exit_profiles, list) or set(exit_profiles) != {"hold_only", "fixed_sl_tp"}:
        raise ConditionalStudyError("exit_profiles must contain hold_only and fixed_sl_tp")
    if len(exit_profiles) != 2:
        raise ConditionalStudyError("exit_profiles must not contain duplicates")

    folds = _mapping(root.get("folds"), name="folds")
    exploration_end_ms = _timestamp(
        folds.get("exploration_end_exclusive"), name="folds.exploration_end_exclusive"
    )
    validation_end_ms = _timestamp(
        folds.get("validation_end_exclusive"), name="folds.validation_end_exclusive"
    )
    confirmation_start_ms = _timestamp(
        folds.get("confirmation_start_inclusive"), name="folds.confirmation_start_inclusive"
    )
    if not (
        start_ms < exploration_end_ms < validation_end_ms == confirmation_start_ms < end_ms
    ):
        raise ConditionalStudyError("fold boundaries must be ordered with confirmation after validation")

    values = root.get("quantiles")
    if not isinstance(values, list) or not values:
        raise ConditionalStudyError("quantiles must be a non-empty list")
    quantiles = tuple(_decimal(value, name="quantiles") for value in values)
    if any(value <= 0 or value >= 1 for value in quantiles) or tuple(sorted(quantiles)) != quantiles:
        raise ConditionalStudyError("quantiles must be increasing values between zero and one")

    specs = root.get("candidate_specs")
    if not isinstance(specs, list) or tuple(specs) != ("A", "B", "C", "D", "E"):
        raise ConditionalStudyError("candidate_specs must be A through E in order")

    policy = _mapping(root.get("promotion_policy"), name="promotion_policy")
    promotion_policy = PromotionPolicy(
        minimum_validation_trades=_positive_int(
            policy.get("minimum_validation_trades"), name="promotion_policy.minimum_validation_trades"
        ),
        minimum_side_trades=_positive_int(
            policy.get("minimum_side_trades"), name="promotion_policy.minimum_side_trades"
        ),
        positive_validation_month_fraction=_decimal(
            policy.get("positive_validation_month_fraction"),
            name="promotion_policy.positive_validation_month_fraction",
        ),
        max_drawdown_pct=_decimal(
            policy.get("max_drawdown_pct"), name="promotion_policy.max_drawdown_pct"
        ),
        both_directions_positive=_bool(
            policy.get("both_directions_positive", True),
            name="promotion_policy.both_directions_positive",
        ),
        no_drawdown_stop=_bool(
            policy.get("no_drawdown_stop", True),
            name="promotion_policy.no_drawdown_stop",
        ),
    )
    if not 0 < promotion_policy.positive_validation_month_fraction <= 1:
        raise ConditionalStudyError("promotion_policy positive month fraction must be between zero and one")
    if promotion_policy.max_drawdown_pct <= 0:
        raise ConditionalStudyError("promotion_policy max drawdown must be positive")

    return ConditionalStudyConfig(
        path=path.resolve(),
        experiment_id=_string(root.get("experiment_id"), name="experiment_id"),
        base_config_path=base_config_path,
        base_config=base_config,
        start_ms=start_ms,
        end_ms=end_ms,
        exploration_end_ms=exploration_end_ms,
        validation_end_ms=validation_end_ms,
        confirmation_start_ms=confirmation_start_ms,
        holds_ms=holds_ms,
        exit_profiles=tuple(exit_profiles),
        quantiles=quantiles,
        candidate_specs=tuple(specs),
        promotion_policy=promotion_policy,
        raw=root,
    )


def iter_conditional_labels(
    *,
    candles: list[NormalizedCandle],
    funding: FundingSeries | None,
    study_config: ConditionalStudyConfig,
) -> Iterator[ConditionalLabel]:
    """Yield every requested label slot, retaining unusable observations explicitly."""

    if not candles:
        raise ConditionalStudyError("at least one candle is required")
    try:
        validate_candles_match_market(
            candles,
            venue=study_config.base_config.market_venue,
            symbol=study_config.base_config.symbol,
        )
    except ResearchDataError as error:
        raise ConditionalStudyError("candle market does not match the base config") from error

    series = CandleSeries(candles)
    sides = (Side.LONG, Side.SHORT)
    for decision_time_ms in range(
        study_config.start_ms, study_config.end_ms, RESEARCH_DECISION_INTERVAL_MS
    ):
        try:
            features = series.build_features(decision_time_ms=decision_time_ms)
            entry_index = series.entry_index(
                decision_time_ms=decision_time_ms,
                config=study_config.base_config.execution,
            )
        except ResearchDataError as error:
            yield from _incomplete_labels(
                decision_time_ms=decision_time_ms,
                sides=sides,
                holds_ms=study_config.holds_ms,
                exit_profiles=study_config.exit_profiles,
                status="incomplete_price",
                reason=str(error),
                features=None,
            )
            continue

        reference_quantity = study_config.base_config.reference_notional / Decimal(
            str(candles[entry_index].open)
        )
        for side in sides:
            decision = TradeDecision(
                side=side,
                stop_loss_pct=Decimal("0.30"),
                take_profit_pct=Decimal("0.60"),
                confidence=Decimal("0.5"),
                thesis="conditional edge label",
                would_abstain=False,
                abstain_reason=None,
            )
            for hold_ms in study_config.holds_ms:
                for exit_profile in study_config.exit_profiles:
                    execution = replace(
                        study_config.base_config.execution,
                        max_hold_ms=hold_ms,
                        exit_policy=exit_profile,
                    )
                    try:
                        episode = simulate_episode_from_entry(
                            decision=decision,
                            decision_time_ms=decision_time_ms,
                            quantity=reference_quantity,
                            candles=candles,
                            entry_index=entry_index,
                            config=execution,
                        )
                    except SimulationError as error:
                        yield ConditionalLabel(
                            decision_time_ms=decision_time_ms,
                            side=side,
                            hold_ms=hold_ms,
                            exit_profile=exit_profile,
                            status="incomplete_price",
                            features=features,
                            reason=str(error),
                        )
                        continue

                    if candles[entry_index].venue == BINANCE_USDM_VENUE and funding is None:
                        yield ConditionalLabel(
                            decision_time_ms=decision_time_ms,
                            side=side,
                            hold_ms=hold_ms,
                            exit_profile=exit_profile,
                            status="incomplete_funding",
                            features=features,
                            reason="Binance USD-M labels require funding data",
                        )
                        continue
                    if funding is not None:
                        try:
                            episode = with_funding(
                                episode,
                                funding.payment(
                                    entry_time_ms=episode.entry_time_ms,
                                    exit_time_ms=episode.exit_time_ms,
                                    notional=episode.entry_price * episode.quantity,
                                    side=side,
                                ),
                            )
                        except FundingDataError as error:
                            yield ConditionalLabel(
                                decision_time_ms=decision_time_ms,
                                side=side,
                                hold_ms=hold_ms,
                                exit_profile=exit_profile,
                                status="incomplete_funding",
                                features=features,
                                reason=str(error),
                            )
                            continue
                    yield ConditionalLabel(
                        decision_time_ms=decision_time_ms,
                        side=side,
                        hold_ms=hold_ms,
                        exit_profile=exit_profile,
                        status="complete",
                        features=features,
                        episode=episode,
                    )


def _incomplete_labels(
    *,
    decision_time_ms: int,
    sides: tuple[Side, ...],
    holds_ms: tuple[int, ...],
    exit_profiles: tuple[str, ...],
    status: str,
    reason: str,
    features: CommonCandleFeatures | None,
) -> Iterator[ConditionalLabel]:
    for side in sides:
        for hold_ms in holds_ms:
            for exit_profile in exit_profiles:
                yield ConditionalLabel(
                    decision_time_ms=decision_time_ms,
                    side=side,
                    hold_ms=hold_ms,
                    exit_profile=exit_profile,
                    status=status,
                    features=features,
                    reason=reason,
                )


def write_conditional_labels(
    *,
    output_dir: Path,
    candles: list[NormalizedCandle],
    funding: FundingSeries | None,
    study_config: ConditionalStudyConfig,
    candle_sha256: str,
    funding_sha256: str | None,
    code_commit_sha: str | None,
) -> ConditionalLabelFiles:
    """Write feature and future-outcome rows separately, without retaining all labels."""

    output_dir.mkdir(parents=True, exist_ok=True)
    features_path = output_dir / "features.jsonl"
    labels_path = output_dir / "labels.jsonl"
    features_tmp = features_path.with_name(f"{features_path.name}.tmp")
    labels_tmp = labels_path.with_name(f"{labels_path.name}.tmp")
    feature_count = 0
    label_count = 0
    status_counts: dict[str, int] = {}
    written_feature_times: set[int] = set()

    try:
        with features_tmp.open("w", encoding="utf-8", newline="\n") as features_handle, labels_tmp.open(
            "w", encoding="utf-8", newline="\n"
        ) as labels_handle:
            for label in iter_conditional_labels(
                candles=candles,
                funding=funding,
                study_config=study_config,
            ):
                if label.features is not None and label.decision_time_ms not in written_feature_times:
                    features_handle.write(
                        _canonical_json(
                            {
                                "decision_time_ms": label.decision_time_ms,
                                **asdict(label.features),
                                "cost_bps": format(
                                    estimated_round_trip_cost_bps(study_config.base_config.execution), "f"
                                ),
                            }
                        )
                        + "\n"
                    )
                    written_feature_times.add(label.decision_time_ms)
                    feature_count += 1
                labels_handle.write(_canonical_json(_label_row(label)) + "\n")
                label_count += 1
                status_counts[label.status] = status_counts.get(label.status, 0) + 1
        features_tmp.replace(features_path)
        labels_tmp.replace(labels_path)
    except Exception:
        features_tmp.unlink(missing_ok=True)
        labels_tmp.unlink(missing_ok=True)
        raise

    quality = "ok" if status_counts.keys() == {"complete"} else "partial"
    common_manifest = {
        "schema_version": 1,
        "experiment_id": study_config.experiment_id,
        "study_config_sha256": _canonical_config_sha256(study_config.raw),
        "candle_sha256": candle_sha256,
        "funding_sha256": funding_sha256,
        "code_commit_sha": code_commit_sha,
        "market": {
            "venue": study_config.base_config.market_venue,
            "symbol": study_config.base_config.symbol,
            "interval": "1m",
        },
        "requested_start_ms": study_config.start_ms,
        "requested_end_exclusive_ms": study_config.end_ms,
        "holds_ms": list(study_config.holds_ms),
        "exit_profiles": list(study_config.exit_profiles),
        "cost_bps": format(estimated_round_trip_cost_bps(study_config.base_config.execution), "f"),
        "quality": quality,
    }
    features_manifest_path = _write_artifact_manifest(
        features_path,
        {
            **common_manifest,
            "artifact_type": "conditional_features",
            "row_count": feature_count,
        },
    )
    labels_manifest_path = _write_artifact_manifest(
        labels_path,
        {
            **common_manifest,
            "artifact_type": "conditional_labels",
            "row_count": label_count,
            "status_counts": status_counts,
        },
    )
    return ConditionalLabelFiles(
        features_path=features_path,
        labels_path=labels_path,
        features_manifest_path=features_manifest_path,
        labels_manifest_path=labels_manifest_path,
        feature_count=feature_count,
        label_count=label_count,
        status_counts=status_counts,
        quality=quality,
    )


def _label_row(label: ConditionalLabel) -> dict[str, object]:
    episode = label.episode
    notional = episode.entry_price * episode.quantity if episode is not None else None
    net_return_bps = (
        episode.net_pnl / notional * Decimal("10000")
        if episode is not None and notional is not None and notional > 0
        else None
    )
    return {
        "decision_time_ms": label.decision_time_ms,
        "side": label.side.value,
        "hold_ms": label.hold_ms,
        "exit_profile": label.exit_profile,
        "status": label.status,
        "quality": episode.quality if episode is not None else None,
        "reason": label.reason,
        "entry_time_ms": episode.entry_time_ms if episode is not None else None,
        "exit_time_ms": episode.exit_time_ms if episode is not None else None,
        "exit_reason": episode.exit_reason if episode is not None else None,
        "quantity": _decimal_text(episode.quantity) if episode is not None else None,
        "gross_pnl": _decimal_text(episode.gross_pnl) if episode is not None else None,
        "fee": _decimal_text(episode.fee) if episode is not None else None,
        "funding": _decimal_text(episode.funding) if episode is not None else None,
        "net_pnl": _decimal_text(episode.net_pnl) if episode is not None else None,
        "net_return_bps": _decimal_text(net_return_bps) if net_return_bps is not None else None,
    }


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _canonical_config_sha256(config: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(config).encode("utf-8")).hexdigest()


def _write_artifact_manifest(path: Path, payload: dict[str, object]) -> Path:
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest = {
        "schema_version": 1,
        **payload,
        "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    temporary = manifest_path.with_name(f"{manifest_path.name}.tmp")
    temporary.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest_path
