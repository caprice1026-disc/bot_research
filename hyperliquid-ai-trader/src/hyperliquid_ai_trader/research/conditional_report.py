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


@dataclass(frozen=True)
class ConditionalDiagnosticFiles:
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
    validation_stats = _load_selected_validation_stats(candidates_path)
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
    summaries = []
    for result in results:
        summary = _replay_summary(result)
        summary["validation"] = validation_stats[result.candidate.candidate_id]
        summary["promotion_checks"] = _promotion_checks(summary, study_config)
        summaries.append(summary)
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
            "both_directions_positive": study_config.promotion_policy.both_directions_positive,
            "no_drawdown_stop": study_config.promotion_policy.no_drawdown_stop,
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
        "risk_rejection_reason_counts": _risk_rejection_reason_counts(result.decisions),
    }


def _status_counts(rows: tuple[dict[str, object], ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _risk_rejection_reason_counts(rows: tuple[dict[str, object], ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("status") != "risk_rejected":
            continue
        reason = str(row.get("reason", "unknown"))
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _promoted_candidates(
    summaries: list[dict[str, object]], study_config: ConditionalStudyConfig
) -> list[str]:
    """Apply validation gates separately from the sequential-account safety gate."""

    promoted: list[str] = []
    for summary in summaries:
        checks = _promotion_checks(summary, study_config)
        if all(checks.values()):
            promoted.append(str(summary["candidate_id"]))
    return promoted


def _promotion_checks(
    summary: Mapping[str, object], study_config: ConditionalStudyConfig
) -> dict[str, bool]:
    """Return every declared promotion gate so reports cannot hide a failed condition."""

    policy = study_config.promotion_policy
    try:
        validation = summary["validation"]
        if not isinstance(validation, Mapping):
            raise TypeError
        side = validation["side"]
        monthly = validation["monthly_net_pnl"]
        if not isinstance(side, Mapping) or not isinstance(monthly, Mapping):
            raise TypeError
        long = side["long"]
        short = side["short"]
        if not isinstance(long, Mapping) or not isinstance(short, Mapping):
            raise TypeError
        positive_month_fraction = (
            Decimal(sum(Decimal(str(value)) > 0 for value in monthly.values())) / Decimal(len(monthly))
            if monthly
            else Decimal("0")
        )
        risk_rejections = summary.get("risk_rejection_reason_counts", {})
        if not isinstance(risk_rejections, Mapping):
            raise TypeError
    except (KeyError, TypeError, ValueError) as error:
        raise ConditionalReportError("candidate summary lacks validation promotion evidence") from error

    checks = {
        "validation_trade_count": int(validation["trade_count"]) >= policy.minimum_validation_trades,
        "long_minimum_trade_count": int(long["trade_count"]) >= policy.minimum_side_trades,
        "short_minimum_trade_count": int(short["trade_count"]) >= policy.minimum_side_trades,
        "positive_validation_month_fraction": positive_month_fraction
        >= policy.positive_validation_month_fraction,
        "account_max_drawdown_below_limit": Decimal(str(summary["max_drawdown_pct"]))
        < policy.max_drawdown_pct,
    }
    if policy.both_directions_positive:
        checks["validation_long_net_pnl_positive"] = Decimal(str(long["net_pnl"])) > 0
        checks["validation_short_net_pnl_positive"] = Decimal(str(short["net_pnl"])) > 0
    if policy.no_drawdown_stop:
        checks["account_not_stopped_at_max_drawdown"] = int(risk_rejections.get("max_drawdown", 0)) == 0
    return checks


def _load_selected_validation_stats(path: Path) -> dict[str, dict[str, object]]:
    """Load persisted validation evidence; old artifacts without monthly data are unsafe."""

    try:
        verify_artifact(path, expected_type="conditional_candidates")
        payload = json.loads(path.read_text(encoding="utf-8"))
        selected_ids = payload["selected_candidate_ids"]
        rows = payload["candidates"]
    except (ArtifactError, OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ConditionalReportError("candidate validation evidence is invalid") from error
    if not isinstance(selected_ids, list) or not isinstance(rows, list):
        raise ConditionalReportError("candidate validation evidence rows are invalid")
    selected = set(selected_ids)
    evidence: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("candidate_id") not in selected:
            continue
        try:
            validation = row["fold_stats"]["validation"]
            monthly = validation["monthly_net_pnl"]
        except (KeyError, TypeError) as error:
            raise ConditionalReportError(
                "candidate analysis lacks validation monthly evidence; rerun conditional-analyze"
            ) from error
        if not isinstance(validation, dict) or not isinstance(monthly, dict):
            raise ConditionalReportError("candidate validation evidence is invalid")
        evidence[str(row["candidate_id"])] = validation
    if set(evidence) != selected:
        raise ConditionalReportError("selected candidate validation evidence is incomplete")
    return evidence


def _decompose_episode(row: Mapping[str, object], execution: object) -> dict[str, str]:
    """Recover raw-price PnL from a saved episode written with the shared cost model."""

    try:
        side = str(row["side"])
        quantity = Decimal(str(row["quantity"]))
        gross_pnl = Decimal(str(row["gross_pnl"]))
        fee = Decimal(str(row["fee"]))
        funding = Decimal(str(row["funding"]))
        net_pnl = Decimal(str(row["net_pnl"]))
        fee_rate = Decimal(str(execution.fee_rate))
        spread_bps = Decimal(str(execution.spread_bps))
        slippage_bps = Decimal(str(execution.slippage_bps))
    except (AttributeError, KeyError, ValueError) as error:
        raise ConditionalReportError("episode is missing finite PnL or execution inputs") from error
    if side not in {"long", "short"} or quantity <= 0 or fee_rate <= 0:
        raise ConditionalReportError("episode cannot be decomposed safely")

    direction = Decimal("1") if side == "long" else Decimal("-1")
    executed_notional_sum = fee / fee_rate
    executed_price_difference = gross_pnl / (quantity * direction)
    entry_executed = (executed_notional_sum / quantity - executed_price_difference) / Decimal("2")
    exit_executed = (executed_notional_sum / quantity + executed_price_difference) / Decimal("2")
    spread_half = spread_bps / Decimal("2") / Decimal("10000")
    slippage = slippage_bps / Decimal("10000")
    full = spread_half + slippage

    def raw_prices(adverse: Decimal) -> tuple[Decimal, Decimal]:
        if side == "long":
            return entry_executed / (Decimal("1") + adverse), exit_executed / (Decimal("1") - adverse)
        return entry_executed / (Decimal("1") - adverse), exit_executed / (Decimal("1") + adverse)

    raw_entry, raw_exit = raw_prices(full)
    market_pnl = (raw_exit - raw_entry) * quantity * direction
    spread_entry, spread_exit = _executed_prices(raw_entry, raw_exit, side, spread_half)
    gross_with_spread = (spread_exit - spread_entry) * quantity * direction
    spread_impact = gross_with_spread - market_pnl
    slippage_impact = gross_pnl - gross_with_spread
    reconciliation_error = net_pnl - (market_pnl + spread_impact + slippage_impact - fee + funding)
    return {
        "market_pnl_before_execution": _decimal_compact(market_pnl),
        "spread_impact": _decimal_compact(spread_impact),
        "slippage_impact": _decimal_compact(slippage_impact),
        "fee": _decimal_compact(fee),
        "funding": _decimal_compact(funding),
        "net_pnl": _decimal_compact(net_pnl),
        "reconciliation_error": _decimal_compact(reconciliation_error),
    }


def _executed_prices(
    raw_entry: Decimal, raw_exit: Decimal, side: str, adverse: Decimal
) -> tuple[Decimal, Decimal]:
    if side == "long":
        return raw_entry * (Decimal("1") + adverse), raw_exit * (Decimal("1") - adverse)
    return raw_entry * (Decimal("1") - adverse), raw_exit * (Decimal("1") + adverse)


def _decimal_compact(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _decimal_close(left: Decimal, right: Decimal) -> bool:
    """Allow only arithmetic reconstruction residue, never a cent-scale accounting mismatch."""

    return abs(left - right) <= Decimal("0.000000000000000001")


def write_conditional_diagnostics(
    *,
    study_config: ConditionalStudyConfig,
    analysis_dir: Path,
    replay_dir: Path,
    output_dir: Path,
) -> ConditionalDiagnosticFiles:
    """Audit selected-candidate promotion evidence and decompose saved Replay PnL."""

    candidates_path = analysis_dir / "candidates.json"
    replay_report_path = replay_dir / "report.json"
    episodes_path = replay_dir / "episodes.jsonl"
    try:
        verify_artifact(candidates_path, expected_type="conditional_candidates")
        verify_artifact(replay_report_path, expected_type="conditional_replay_report")
        verify_artifact(episodes_path, expected_type="conditional_replay_episodes")
        candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        replay_payload = json.loads(replay_report_path.read_text(encoding="utf-8"))
    except (ArtifactError, OSError, json.JSONDecodeError) as error:
        raise ConditionalReportError("diagnostic source artifacts are invalid") from error
    expected_config_hash = _config_sha256(study_config.raw)
    if (
        candidates_payload.get("study_config_sha256") != expected_config_hash
        or replay_payload.get("study_config_sha256") != expected_config_hash
        or replay_payload.get("source_candidates_sha256") != sha256_path(candidates_path)
    ):
        raise ConditionalReportError("diagnostic source artifacts do not share the requested study config")

    candidate_rows = _candidate_rows_by_id(candidates_payload)
    summaries = replay_payload.get("candidates")
    if not isinstance(summaries, list):
        raise ConditionalReportError("replay report candidates are invalid")
    episodes_by_candidate = _read_replay_episodes(episodes_path, set(candidate_rows))
    candidate_reports: list[dict[str, object]] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            raise ConditionalReportError("replay candidate summary is invalid")
        candidate_id = str(summary.get("candidate_id", ""))
        candidate = candidate_rows.get(candidate_id)
        if candidate is None:
            raise ConditionalReportError("replay report includes an unknown candidate")
        checks = _promotion_checks(summary, study_config)
        episodes = episodes_by_candidate.get(candidate_id, [])
        pnl = _pnl_breakdown(episodes, study_config.base_config.execution)
        if not _decimal_close(Decimal(str(pnl["net_pnl"])), Decimal(str(summary["net_pnl"]))):
            raise ConditionalReportError("episode PnL does not reconcile with its replay summary")
        candidate_reports.append(
            {
                "candidate_id": candidate_id,
                "candidate": {
                    "name": candidate["name"],
                    "hold_ms": candidate["hold_ms"],
                    "exit_profile": candidate["exit_profile"],
                    "threshold_quantile": candidate["threshold_quantile"],
                },
                "validation": summary["validation"],
                "promotion_checks": checks,
                "account_replay": {
                    "status": summary["status"],
                    "trade_count": summary["trade_count"],
                    "net_pnl": summary["net_pnl"],
                    "max_drawdown_pct": summary["max_drawdown_pct"],
                    "risk_rejection_reason_counts": summary["risk_rejection_reason_counts"],
                    "observed_episode_window": _episode_window(episodes),
                },
                "pnl": pnl,
                "by_side": _pnl_breakdown_by(episodes, study_config.base_config.execution, "side"),
                "by_exit_month": _pnl_breakdown_by_exit_month(
                    episodes, study_config.base_config.execution
                ),
            }
        )
    computed_promoted = [
        str(row["candidate_id"]) for row in candidate_reports if all(row["promotion_checks"].values())
    ]
    if computed_promoted != replay_payload.get("promoted_candidate_ids"):
        raise ConditionalReportError("replay promotion result does not match its declared gate evidence")

    status = str(replay_payload.get("status", "partial"))
    conclusion = str(replay_payload.get("conclusion", "inconclusive"))
    report = {
        "schema_version": 1,
        "artifact_type": "conditional_diagnostic_report",
        "study_config_sha256": expected_config_hash,
        "source_candidates_sha256": sha256_path(candidates_path),
        "source_replay_report_sha256": sha256_path(replay_report_path),
        "source_episodes_sha256": sha256_path(episodes_path),
        "status": status,
        "conclusion": conclusion,
        "cost_model": _cost_model_summary(study_config),
        "promoted_candidate_ids": computed_promoted,
        "candidates": candidate_reports,
        "interpretation": {
            "price_component": "market_pnl_before_execution is reconstructed before configured spread and slippage.",
            "month_definition": "by_exit_month attributes realized account PnL to the UTC exit month.",
            "account_scope": "Each candidate is a separate sequential account replay, not a combined portfolio.",
            "partial_scope": "A partial status means this diagnostic cannot upgrade the study to supported.",
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "diagnostic.json"
    _write_json_artifact(report_path, report)
    report_markdown_path = output_dir / "diagnostic.md"
    report_markdown_path.write_text(_diagnostic_markdown(report), encoding="utf-8")
    _write_json_artifact_manifest(report_markdown_path, "conditional_diagnostic_markdown")
    return ConditionalDiagnosticFiles(
        report_path=report_path,
        report_markdown_path=report_markdown_path,
        status=status,
        conclusion=conclusion,
    )


_PNL_KEYS = (
    "market_pnl_before_execution",
    "spread_impact",
    "slippage_impact",
    "gross_pnl_after_execution",
    "fee",
    "funding",
    "net_pnl",
)


def _candidate_rows_by_id(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        raise ConditionalReportError("candidate analysis rows are invalid")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str):
            raise ConditionalReportError("candidate analysis row is invalid")
        result[str(row["candidate_id"])] = row
    return result


def _read_replay_episodes(path: Path, candidate_ids: set[str]) -> dict[str, list[dict[str, object]]]:
    rows: dict[str, list[dict[str, object]]] = {candidate_id: [] for candidate_id in candidate_ids}
    try:
        handle = path.open(encoding="utf-8")
    except OSError as error:
        raise ConditionalReportError("cannot read replay episode rows") from error
    with handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
                candidate_id = row["candidate_id"]
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise ConditionalReportError(f"invalid replay episode row {line_number}") from error
            if not isinstance(row, dict) or not isinstance(candidate_id, str) or candidate_id not in rows:
                raise ConditionalReportError(f"replay episode candidate is invalid at {line_number}")
            rows[candidate_id].append(row)
    return rows


def _pnl_breakdown(rows: list[Mapping[str, object]], execution: object) -> dict[str, object]:
    values = _empty_pnl_values()
    for row in rows:
        _add_episode_pnl(values, row, execution)
    return _pnl_values_row(values)


def _pnl_breakdown_by(
    rows: list[Mapping[str, object]], execution: object, key: str
) -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for row in rows:
        name = str(row.get(key, "unknown"))
        bucket = values.setdefault(name, _empty_pnl_values())
        _add_episode_pnl(bucket, row, execution)
    return {name: _pnl_values_row(bucket) for name, bucket in sorted(values.items())}


def _pnl_breakdown_by_exit_month(
    rows: list[Mapping[str, object]], execution: object
) -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for row in rows:
        try:
            month = _month_key(int(row["exit_time_ms"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ConditionalReportError("replay episode exit time is invalid") from error
        bucket = values.setdefault(month, _empty_pnl_values())
        _add_episode_pnl(bucket, row, execution)
    return {month: _pnl_values_row(bucket) for month, bucket in sorted(values.items())}


def _empty_pnl_values() -> dict[str, object]:
    return {"trade_count": 0, **{name: Decimal("0") for name in _PNL_KEYS}, "reconciliation_error": Decimal("0")}


def _add_episode_pnl(values: dict[str, object], row: Mapping[str, object], execution: object) -> None:
    components = _decompose_episode(row, execution)
    values["trade_count"] = int(values["trade_count"]) + 1
    for name in ("market_pnl_before_execution", "spread_impact", "slippage_impact", "fee", "funding", "net_pnl"):
        values[name] = Decimal(str(values[name])) + Decimal(components[name])
    values["gross_pnl_after_execution"] = Decimal(str(values["gross_pnl_after_execution"])) + Decimal(
        str(row["gross_pnl"])
    )
    values["reconciliation_error"] = Decimal(str(values["reconciliation_error"])) + Decimal(
        components["reconciliation_error"]
    )


def _pnl_values_row(values: Mapping[str, object]) -> dict[str, object]:
    trade_count = int(values["trade_count"])
    return {
        "trade_count": trade_count,
        **{name: _decimal_compact(Decimal(str(values[name]))) for name in _PNL_KEYS},
        "net_expectancy_per_trade": _decimal_compact(Decimal(str(values["net_pnl"])) / trade_count)
        if trade_count
        else None,
        "reconciliation_error": _decimal_compact(Decimal(str(values["reconciliation_error"]))),
    }


def _episode_window(rows: list[Mapping[str, object]]) -> dict[str, object] | None:
    if not rows:
        return None
    try:
        entries = [int(row["entry_time_ms"]) for row in rows]
        exits = [int(row["exit_time_ms"]) for row in rows]
    except (KeyError, TypeError, ValueError) as error:
        raise ConditionalReportError("replay episode time is invalid") from error
    return {
        "first_entry_time_ms": min(entries),
        "last_exit_time_ms": max(exits),
        "first_entry_utc": _utc_text(min(entries)),
        "last_exit_utc": _utc_text(max(exits)),
    }


def _cost_model_summary(study_config: ConditionalStudyConfig) -> dict[str, str]:
    execution = study_config.base_config.execution
    reference_notional = study_config.base_config.reference_notional
    fee_bps = execution.fee_rate * Decimal("2") * Decimal("10000")
    spread_bps = execution.spread_bps
    slippage_bps = execution.slippage_bps * Decimal("2")
    return {
        "reference_notional_usd": _decimal_compact(reference_notional),
        "fee_round_trip_bps": _decimal_compact(fee_bps),
        "spread_round_trip_bps": _decimal_compact(spread_bps),
        "slippage_round_trip_bps": _decimal_compact(slippage_bps),
        "total_round_trip_bps": _decimal_compact(fee_bps + spread_bps + slippage_bps),
        "reference_fee_usd": _decimal_compact(reference_notional * fee_bps / Decimal("10000")),
        "reference_spread_usd": _decimal_compact(reference_notional * spread_bps / Decimal("10000")),
        "reference_slippage_usd": _decimal_compact(
            reference_notional * slippage_bps / Decimal("10000")
        ),
        "reference_total_cost_usd": _decimal_compact(
            reference_notional * (fee_bps + spread_bps + slippage_bps) / Decimal("10000")
        ),
    }


def _utc_text(time_ms: int) -> str:
    return datetime.fromtimestamp(time_ms / 1_000, timezone.utc).isoformat().replace("+00:00", "Z")


def _diagnostic_markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# Conditional Edge Diagnostic",
        "",
        f"status: {report['status']}",
        f"conclusion: {report['conclusion']}",
        "",
        "## Cost model",
        "",
        "Configured round-trip cost at the fixed 250 USD reference notional is split into fee, spread, and slippage below. Funding is not part of this configured estimate.",
        "",
    ]
    cost = report["cost_model"]
    assert isinstance(cost, Mapping)
    lines.extend(
        [
            f"- fee: {cost['fee_round_trip_bps']} bps / {cost['reference_fee_usd']} USD",
            f"- spread: {cost['spread_round_trip_bps']} bps / {cost['reference_spread_usd']} USD",
            f"- slippage: {cost['slippage_round_trip_bps']} bps / {cost['reference_slippage_usd']} USD",
            f"- total: {cost['total_round_trip_bps']} bps / {cost['reference_total_cost_usd']} USD",
            "",
            "## Promotion audit",
            "",
            "Validation evidence is evaluated separately from the sequential account Replay. A Replay that stops at the drawdown limit is shown as an account-safety failure, not as a substitute for validation performance.",
            "",
            "| Candidate | Validation trades | LONG / SHORT trades | Passed all gates | Account window | Account net PnL |",
            "| --- | ---: | ---: | --- | --- | ---: |",
        ]
    )
    candidates = report["candidates"]
    assert isinstance(candidates, list)
    for row in candidates:
        assert isinstance(row, Mapping)
        validation = row["validation"]
        account = row["account_replay"]
        checks = row["promotion_checks"]
        assert isinstance(validation, Mapping) and isinstance(account, Mapping) and isinstance(checks, Mapping)
        side = validation["side"]
        assert isinstance(side, Mapping)
        window = account["observed_episode_window"]
        window_text = "none" if window is None else str(window["first_entry_utc"])[:10] + " to " + str(window["last_exit_utc"])[:10]
        lines.append(
            f"| {row['candidate_id']} | {validation['trade_count']} | {side['long']['trade_count']} / {side['short']['trade_count']} | {'yes' if all(checks.values()) else 'no'} | {window_text} | {account['net_pnl']} |"
        )
    lines.extend(
        [
            "",
            "## PnL decomposition",
            "",
            "`market_pnl_before_execution` is reconstructed from saved fills before the configured spread/slippage adjustment. The following candidates are separate account replays and must not be added as a portfolio.",
            "",
            "| Candidate | Market before execution | Spread | Slippage | Fee | Funding | Net |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in candidates:
        assert isinstance(row, Mapping)
        pnl = row["pnl"]
        assert isinstance(pnl, Mapping)
        lines.append(
            f"| {row['candidate_id']} | {pnl['market_pnl_before_execution']} | {pnl['spread_impact']} | {pnl['slippage_impact']} | -{pnl['fee']} | {pnl['funding']} | {pnl['net_pnl']} |"
        )
    lines.extend(
        [
            "",
            "The report attributes realized PnL to the UTC exit month. Any `partial` status remains inconclusive even when all displayed gate checks are false.",
            "",
        ]
    )
    return "\n".join(lines)


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
                month = _month_key(int(label.get("entry_time_ms", time_ms)))
                monthly = stat["monthly_net_pnl"]
                monthly[month] = monthly.get(month, Decimal("0")) + pnl
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
        "monthly_net_pnl": {},
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
            "monthly_net_pnl": {
                month: _decimal(net_pnl)
                for month, net_pnl in sorted(values["monthly_net_pnl"].items())
            },
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
