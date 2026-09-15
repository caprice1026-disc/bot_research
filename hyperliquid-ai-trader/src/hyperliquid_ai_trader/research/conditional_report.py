"""Offline aggregation for the conditional-edge study artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .artifacts import ArtifactError, sha256_path, verify_artifact
from .conditional_edge import (
    CandidateSpec,
    ConditionalStudyConfig,
    ConditionalStudyError,
    candidate_decision,
    derive_regime_features,
    fit_regime_thresholds,
)
from .conditional_replay import CandidateReplayResult, run_candidate_replay


class ConditionalReportError(ValueError):
    """Raised when conditional study artifacts cannot be analyzed safely."""


@dataclass(frozen=True)
class ConditionalAnalysisFiles:
    regime_summary_path: Path
    candidates_path: Path
    quality: str
    selected_candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConditionalReplayFiles:
    episodes_path: Path
    decisions_path: Path
    report_path: Path
    report_markdown_path: Path
    status: str
    conclusion: str


def analyze_conditional_study(
    *,
    study_config: ConditionalStudyConfig,
    input_dir: Path,
    output_dir: Path,
) -> ConditionalAnalysisFiles:
    """Fit prior-only thresholds and compare the predeclared candidate families."""

    features_path = input_dir / "features.jsonl"
    labels_path = input_dir / "labels.jsonl"
    try:
        features_manifest = verify_artifact(features_path, expected_type="conditional_features")
        labels_manifest = verify_artifact(labels_path, expected_type="conditional_labels")
    except ArtifactError as error:
        raise ConditionalReportError("conditional input artifacts are invalid") from error
    expected_config_hash = _config_sha256(study_config.raw)
    if (
        features_manifest.get("study_config_sha256") != expected_config_hash
        or labels_manifest.get("study_config_sha256") != expected_config_hash
    ):
        raise ConditionalReportError("conditional input artifacts use another study config")
    if features_manifest.get("candle_sha256") != labels_manifest.get("candle_sha256"):
        raise ConditionalReportError("feature and label candle hashes differ")

    features = _read_features(features_path)
    training = [
        derive_regime_features(row, study_config.base_config.execution)
        for time_ms, row in features.items()
        if study_config.start_ms <= time_ms < study_config.exploration_end_ms
    ]
    thresholds = fit_regime_thresholds(training, study_config.quantiles)
    thresholds_by_fold = _fit_prior_thresholds(features, study_config)
    cost_bps = derive_regime_features(next(iter(features.values())), study_config.base_config.execution)[
        "cost_bps"
    ]
    assert isinstance(cost_bps, Decimal)
    candidates = _candidate_variants(study_config, cost_bps)
    stats = _collect_candidate_stats(
        labels_path=labels_path,
        features=features,
        study_config=study_config,
        candidates=candidates,
        exploration_thresholds=thresholds,
        thresholds_by_fold=thresholds_by_fold,
    )
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            stats[candidate.candidate_id]["exploration"]["net_pnl"],
            stats[candidate.candidate_id]["exploration"]["trade_count"],
            candidate.candidate_id,
        ),
        reverse=True,
    )
    selected = tuple(
        candidate
        for candidate in ranked
        if stats[candidate.candidate_id]["exploration"]["trade_count"] > 0
    )[:4]
    quality = str(labels_manifest.get("quality", "partial"))
    output_dir.mkdir(parents=True, exist_ok=True)
    regime_summary_path = output_dir / "regime_summary.json"
    candidates_path = output_dir / "candidates.json"
    _write_json_artifact(
        regime_summary_path,
        {
            "schema_version": 1,
            "artifact_type": "conditional_regime_summary",
            "study_config_sha256": expected_config_hash,
            "source_features_sha256": sha256_path(features_path),
            "source_labels_sha256": sha256_path(labels_path),
            "quality": quality,
            "feature_count": len(features),
            "training_feature_count": len(training),
            "thresholds": _threshold_rows(thresholds),
            "prior_thresholds_by_fold": {
                name: _threshold_rows(value) for name, value in thresholds_by_fold.items()
            },
            "alignment_counts": _alignment_counts(features, study_config),
        },
    )
    _write_json_artifact(
        candidates_path,
        {
            "schema_version": 1,
            "artifact_type": "conditional_candidates",
            "study_config_sha256": expected_config_hash,
            "source_features_sha256": sha256_path(features_path),
            "source_labels_sha256": sha256_path(labels_path),
            "quality": quality,
            "selection_method": "top_four_exploration_net_pnl_with_positive_trade_count",
            "selected_candidate_ids": [candidate.candidate_id for candidate in selected],
            "candidates": [
                {
                    **_candidate_row(candidate),
                    "fold_stats": _stats_rows(stats[candidate.candidate_id]),
                }
                for candidate in candidates
            ],
        },
    )
    return ConditionalAnalysisFiles(
        regime_summary_path=regime_summary_path,
        candidates_path=candidates_path,
        quality=quality,
        selected_candidate_ids=tuple(candidate.candidate_id for candidate in selected),
    )


def load_selected_candidates(path: Path) -> tuple[CandidateSpec, ...]:
    """Load only the immutable, exploration-selected candidate definitions."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConditionalReportError("cannot read candidate analysis") from error
    if not isinstance(payload, dict) or payload.get("artifact_type") != "conditional_candidates":
        raise ConditionalReportError("candidate analysis artifact type is invalid")
    selected_ids = payload.get("selected_candidate_ids")
    rows = payload.get("candidates")
    if not isinstance(selected_ids, list) or not isinstance(rows, list):
        raise ConditionalReportError("candidate analysis rows are invalid")
    selected = set(selected_ids)
    candidates: list[CandidateSpec] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("candidate_id") not in selected:
            continue
        try:
            candidate = CandidateSpec(
                name=str(row["name"]),
                hold_ms=int(row["hold_ms"]),
                exit_profile=str(row["exit_profile"]),
                threshold_quantile=(
                    Decimal(str(row["threshold_quantile"]))
                    if row.get("threshold_quantile") is not None
                    else None
                ),
                cost_bps=Decimal(str(row["cost_bps"])),
            )
        except (KeyError, ValueError) as error:
            raise ConditionalReportError("candidate definition is invalid") from error
        if candidate.candidate_id != row["candidate_id"]:
            raise ConditionalReportError("candidate ID does not match its definition")
        candidates.append(candidate)
    if {candidate.candidate_id for candidate in candidates} != selected:
        raise ConditionalReportError("selected candidate definitions are incomplete")
    return tuple(candidates)


def write_conditional_replays(
    *,
    study_config: ConditionalStudyConfig,
    candles: list,
    funding: object,
    analysis_dir: Path,
    output_dir: Path,
) -> ConditionalReplayFiles:
    """Run selected candidates and persist account results separately from labels."""

    candidates_path = analysis_dir / "candidates.json"
    thresholds_path = analysis_dir / "regime_summary.json"
    candidates = load_selected_candidates(candidates_path)
    thresholds = _load_replay_thresholds(thresholds_path)
    if not candidates:
        raise ConditionalReportError("candidate analysis selected no candidates")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[CandidateReplayResult] = []
    for candidate in candidates:
        results.append(
            run_candidate_replay(
                candles=candles,
                funding=funding,
                study_config=study_config,
                candidate=candidate,
                thresholds=thresholds["exploration"],
                thresholds_for_time=lambda time_ms, values=thresholds: _replay_thresholds_for_time(
                    time_ms, study_config, values
                ),
            )
        )
    episodes_path = output_dir / "episodes.jsonl"
    decisions_path = output_dir / "decisions.jsonl"
    _write_jsonl(
        episodes_path,
        (
            _episode_row(result.candidate, episode)
            for result in results
            for episode in result.episodes
        ),
    )
    _write_jsonl(
        decisions_path,
        (
            dict(row, candidate_id=result.candidate.candidate_id)
            for result in results
            for row in result.decisions
        ),
    )
    status = "ok" if all(result.status == "ok" for result in results) else "partial"
    summaries = [_replay_summary(result) for result in results]
    promoted = _promoted_candidates(summaries, study_config)
    conclusion = "supported" if status == "ok" and promoted else "inconclusive" if status != "ok" else "no_supported_candidate"
    report_path = output_dir / "report.json"
    report = {
        "schema_version": 1,
        "artifact_type": "conditional_replay_report",
        "study_config_sha256": _config_sha256(study_config.raw),
        "source_candidates_sha256": sha256_path(candidates_path),
        "source_regime_summary_sha256": sha256_path(thresholds_path),
        "status": status,
        "conclusion": conclusion,
        "promotion_policy": {
            "minimum_validation_trades": study_config.promotion_policy.minimum_validation_trades,
            "minimum_side_trades": study_config.promotion_policy.minimum_side_trades,
            "positive_validation_month_fraction": _decimal(
                study_config.promotion_policy.positive_validation_month_fraction
            ),
            "max_drawdown_pct": _decimal(study_config.promotion_policy.max_drawdown_pct),
        },
        "promoted_candidate_ids": promoted,
        "candidates": summaries,
    }
    _write_json_artifact(report_path, report)
    report_markdown_path = output_dir / "report.md"
    report_markdown_path.write_text(
        _report_markdown(status=status, conclusion=conclusion, summaries=summaries), encoding="utf-8"
    )
    _write_json_artifact_manifest(report_markdown_path, "conditional_replay_markdown")
    _write_json_artifact_manifest(episodes_path, "conditional_replay_episodes")
    _write_json_artifact_manifest(decisions_path, "conditional_replay_decisions")
    return ConditionalReplayFiles(
        episodes_path=episodes_path,
        decisions_path=decisions_path,
        report_path=report_path,
        report_markdown_path=report_markdown_path,
        status=status,
        conclusion=conclusion,
    )


def _read_features(path: Path) -> dict[int, dict[str, object]]:
    rows: dict[int, dict[str, object]] = {}
    try:
        lines = path.open(encoding="utf-8")
    except OSError as error:
        raise ConditionalReportError("cannot read feature rows") from error
    with lines:
        for line_number, line in enumerate(lines, start=1):
            try:
                row = json.loads(line)
                time_ms = row["decision_time_ms"]
                if not isinstance(row, dict) or isinstance(time_ms, bool) or not isinstance(time_ms, int):
                    raise TypeError
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise ConditionalReportError(f"invalid feature row {line_number}") from error
            if time_ms in rows:
                raise ConditionalReportError("feature decision times must be unique")
            rows[time_ms] = row
    if not rows:
        raise ConditionalReportError("feature artifact is empty")
    return rows


def _load_replay_thresholds(path: Path) -> dict[str, dict[str, dict[Decimal, Decimal]]]:
    try:
        verify_artifact(path, expected_type="conditional_regime_summary")
        payload = json.loads(path.read_text(encoding="utf-8"))
        prior = payload["prior_thresholds_by_fold"]
        exploration = payload["thresholds"]
    except (ArtifactError, OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ConditionalReportError("regime summary thresholds are invalid") from error
    values = {name: _parse_thresholds(raw) for name, raw in prior.items()}
    values["exploration"] = _parse_thresholds(exploration)
    return values


def _parse_thresholds(raw: object) -> dict[str, dict[Decimal, Decimal]]:
    if not isinstance(raw, dict):
        raise ConditionalReportError("regime threshold rows are invalid")
    try:
        return {
            str(name): {Decimal(str(quantile)): Decimal(str(value)) for quantile, value in row.items()}
            for name, row in raw.items()
            if isinstance(row, dict)
        }
    except ValueError as error:
        raise ConditionalReportError("regime threshold value is invalid") from error


def _replay_thresholds_for_time(
    time_ms: int,
    study_config: ConditionalStudyConfig,
    thresholds: Mapping[str, Mapping[str, Mapping[Decimal, Decimal]]],
) -> Mapping[str, Mapping[Decimal, Decimal]]:
    if time_ms < study_config.exploration_end_ms:
        return thresholds["exploration"]
    if time_ms < study_config.confirmation_start_ms:
        return thresholds[_month_key(time_ms)]
    return thresholds["confirmation"]


def _candidate_variants(
    study_config: ConditionalStudyConfig, cost_bps: Decimal
) -> tuple[CandidateSpec, ...]:
    candidates: list[CandidateSpec] = []
    for hold_ms in study_config.holds_ms:
        for exit_profile in study_config.exit_profiles:
            for name in ("A", "B"):
                candidates.append(CandidateSpec(name, hold_ms, exit_profile, None, cost_bps))
            for name in ("C", "D", "E"):
                for quantile in study_config.quantiles:
                    candidates.append(
                        CandidateSpec(name, hold_ms, exit_profile, quantile, cost_bps)
                    )
    return tuple(candidates)


def _episode_row(candidate: CandidateSpec, episode: object) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "decision_time_ms": episode.decision_time_ms,
        "entry_time_ms": episode.entry_time_ms,
        "exit_time_ms": episode.exit_time_ms,
        "side": episode.side.value,
        "exit_reason": episode.exit_reason,
        "quality": episode.quality,
        "quantity": _decimal(episode.quantity),
        "gross_pnl": _decimal(episode.gross_pnl),
        "fee": _decimal(episode.fee),
        "funding": _decimal(episode.funding),
        "net_pnl": _decimal(episode.net_pnl),
    }


def _replay_summary(result: CandidateReplayResult) -> dict[str, object]:
    peak = result.initial_equity
    equity = result.initial_equity
    max_drawdown = Decimal("0")
    by_side = {"long": Decimal("0"), "short": Decimal("0")}
    by_month: dict[str, Decimal] = {}
    for episode in result.episodes:
        equity += episode.net_pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * Decimal("100"))
        by_side[episode.side.value] += episode.net_pnl
        month = _month_key(episode.entry_time_ms)
        by_month[month] = by_month.get(month, Decimal("0")) + episode.net_pnl
    trade_count = len(result.episodes)
    return {
        "candidate_id": result.candidate.candidate_id,
        "status": result.status,
        "trade_count": trade_count,
        "net_pnl": _decimal(equity - result.initial_equity),
        "net_expectancy_per_trade": _decimal((equity - result.initial_equity) / trade_count)
        if trade_count
        else None,
        "max_drawdown_pct": _decimal(max_drawdown),
        "direction_net_pnl": {side: _decimal(value) for side, value in by_side.items()},
        "monthly_net_pnl": {month: _decimal(value) for month, value in by_month.items()},
        "decision_status_counts": _status_counts(result.decisions),
    }


def _status_counts(rows: tuple[dict[str, object], ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _promoted_candidates(
    summaries: list[dict[str, object]], study_config: ConditionalStudyConfig
) -> list[str]:
    """Apply the predeclared account gate; partial inputs are handled by the caller."""

    policy = study_config.promotion_policy
    promoted: list[str] = []
    for summary in summaries:
        if summary["trade_count"] < policy.minimum_validation_trades:
            continue
        if Decimal(str(summary["max_drawdown_pct"])) >= policy.max_drawdown_pct:
            continue
        direction = summary["direction_net_pnl"]
        if Decimal(str(direction["long"])) <= 0 or Decimal(str(direction["short"])) <= 0:
            continue
        if any(value == "max_drawdown" for value in summary["decision_status_counts"]):
            continue
        promoted.append(str(summary["candidate_id"]))
    return promoted


def _report_markdown(*, status: str, conclusion: str, summaries: list[dict[str, object]]) -> str:
    lines = ["# Conditional Edge Replay", "", f"status: {status}", f"conclusion: {conclusion}", ""]
    for summary in summaries:
        lines.append(
            f"- {summary['candidate_id']}: trades={summary['trade_count']}, net_pnl={summary['net_pnl']}, max_dd_pct={summary['max_drawdown_pct']}"
        )
    return "\n".join(lines) + "\n"


def _fit_prior_thresholds(
    features: Mapping[int, Mapping[str, object]], study_config: ConditionalStudyConfig
) -> dict[str, dict[str, dict[Decimal, Decimal]]]:
    starts = _month_starts(study_config.exploration_end_ms, study_config.confirmation_start_ms)
    values: dict[str, dict[str, dict[Decimal, Decimal]]] = {}
    for start_ms in starts:
        values[_month_key(start_ms)] = fit_regime_thresholds(
            [
                derive_regime_features(row, study_config.base_config.execution)
                for time_ms, row in features.items()
                if study_config.start_ms <= time_ms < start_ms
            ],
            study_config.quantiles,
        )
    values["confirmation"] = fit_regime_thresholds(
        [
            derive_regime_features(row, study_config.base_config.execution)
            for time_ms, row in features.items()
            if study_config.start_ms <= time_ms < study_config.confirmation_start_ms
        ],
        study_config.quantiles,
    )
    return values


def _month_starts(start_ms: int, end_ms: int) -> tuple[int, ...]:
    moment = datetime.fromtimestamp(start_ms / 1_000, timezone.utc)
    starts: list[int] = []
    while int(moment.timestamp() * 1_000) < end_ms:
        starts.append(int(moment.timestamp() * 1_000))
        year = moment.year + (1 if moment.month == 12 else 0)
        month = 1 if moment.month == 12 else moment.month + 1
        moment = datetime(year, month, 1, tzinfo=timezone.utc)
    return tuple(starts)


def _fold_and_thresholds(
    time_ms: int,
    study_config: ConditionalStudyConfig,
    exploration_thresholds: Mapping[str, Mapping[Decimal, Decimal]],
    thresholds_by_fold: Mapping[str, Mapping[str, Mapping[Decimal, Decimal]]],
) -> tuple[str, Mapping[str, Mapping[Decimal, Decimal]]]:
    if time_ms < study_config.exploration_end_ms:
        return "exploration", exploration_thresholds
    if time_ms < study_config.confirmation_start_ms:
        return "validation", thresholds_by_fold[_month_key(time_ms)]
    return "confirmation", thresholds_by_fold["confirmation"]


def _collect_candidate_stats(
    *,
    labels_path: Path,
    features: Mapping[int, Mapping[str, object]],
    study_config: ConditionalStudyConfig,
    candidates: tuple[CandidateSpec, ...],
    exploration_thresholds: Mapping[str, Mapping[Decimal, Decimal]],
    thresholds_by_fold: Mapping[str, Mapping[str, Mapping[Decimal, Decimal]]],
) -> dict[str, dict[str, dict[str, object]]]:
    grouped: dict[tuple[int, str], tuple[CandidateSpec, ...]] = {}
    for candidate in candidates:
        key = (candidate.hold_ms, candidate.exit_profile)
        grouped[key] = grouped.get(key, ()) + (candidate,)
    stats = {
        candidate.candidate_id: {
            fold: _empty_stats() for fold in ("exploration", "validation", "confirmation")
        }
        for candidate in candidates
    }
    with labels_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                label = json.loads(line)
                time_ms = label["decision_time_ms"]
                side = label["side"]
                key = (int(label["hold_ms"]), str(label["exit_profile"]))
                status = label["status"]
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                raise ConditionalReportError(f"invalid label row {line_number}") from error
            row = features.get(time_ms)
            candidate_group = grouped.get(key, ())
            if row is None or not candidate_group:
                continue
            fold, fold_thresholds = _fold_and_thresholds(
                time_ms, study_config, exploration_thresholds, thresholds_by_fold
            )
            for candidate in candidate_group:
                decision = candidate_decision(candidate, row, fold_thresholds)
                if decision.would_abstain or decision.side.value != side:
                    continue
                stat = stats[candidate.candidate_id][fold]
                stat["signal_count"] += 1
                if status != "complete":
                    stat["incomplete_signals"] += 1
                    continue
                try:
                    pnl = Decimal(str(label["net_pnl"]))
                except (KeyError, ValueError) as error:
                    raise ConditionalReportError(f"complete label is missing net PnL at {line_number}") from error
                stat["trade_count"] += 1
                stat["net_pnl"] += pnl
                side_stats = stat["side"][side]
                side_stats["trade_count"] += 1
                side_stats["net_pnl"] += pnl
    return stats


def _empty_stats() -> dict[str, object]:
    return {
        "signal_count": 0,
        "incomplete_signals": 0,
        "trade_count": 0,
        "net_pnl": Decimal("0"),
        "side": {
            "long": {"trade_count": 0, "net_pnl": Decimal("0")},
            "short": {"trade_count": 0, "net_pnl": Decimal("0")},
        },
    }


def _stats_rows(stats: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    rows: dict[str, object] = {}
    for fold, values in stats.items():
        trade_count = int(values["trade_count"])
        rows[fold] = {
            "signal_count": values["signal_count"],
            "incomplete_signals": values["incomplete_signals"],
            "trade_count": trade_count,
            "net_pnl": _decimal(values["net_pnl"]),
            "net_expectancy_per_trade": _decimal(values["net_pnl"] / trade_count)
            if trade_count
            else None,
            "side": {
                name: {
                    "trade_count": side_values["trade_count"],
                    "net_pnl": _decimal(side_values["net_pnl"]),
                }
                for name, side_values in values["side"].items()
            },
        }
    return rows


def _threshold_rows(
    thresholds: Mapping[str, Mapping[Decimal, Decimal]]
) -> dict[str, dict[str, str]]:
    return {
        name: {_decimal(quantile): _decimal(value) for quantile, value in values.items()}
        for name, values in thresholds.items()
    }


def _candidate_row(candidate: CandidateSpec) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "hold_ms": candidate.hold_ms,
        "exit_profile": candidate.exit_profile,
        "threshold_quantile": _decimal(candidate.threshold_quantile)
        if candidate.threshold_quantile is not None
        else None,
        "cost_bps": _decimal(candidate.cost_bps),
    }


def _alignment_counts(
    features: Mapping[int, Mapping[str, object]], study_config: ConditionalStudyConfig
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in features.values():
        alignment = str(derive_regime_features(row, study_config.base_config.execution)["alignment"])
        counts[alignment] = counts.get(alignment, 0) + 1
    return counts


def _month_key(time_ms: int) -> str:
    return datetime.fromtimestamp(time_ms / 1_000, timezone.utc).strftime("%Y-%m")


def _decimal(value: object) -> str:
    assert isinstance(value, Decimal)
    return format(value, "f")


def _config_sha256(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(config).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _write_json_artifact(path: Path, payload: dict[str, object]) -> None:
    text = _canonical_json(payload) + "\n"
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    _write_json_artifact_manifest(path, str(payload["artifact_type"]))


def _write_jsonl(path: Path, rows: object) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_canonical_json(row) + "\n")
    temporary.replace(path)


def _write_json_artifact_manifest(path: Path, artifact_type: str) -> None:
    manifest = {
        "schema_version": 1,
        "artifact_type": artifact_type,
        "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    temporary_manifest = manifest_path.with_name(f"{manifest_path.name}.tmp")
    temporary_manifest.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    temporary_manifest.replace(manifest_path)
