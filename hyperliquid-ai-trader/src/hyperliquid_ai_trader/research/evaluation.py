"""Evaluate validated point decisions without an account, provider, or order API."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any

from ..agents import FunctionCall
from ..models import TradeDecision
from .binance import BINANCE_USDM_VENUE, FundingDataError, FundingSeries
from .config import ResearchConfig
from .data import (
    CandleSeries,
    NormalizedCandle,
    ResearchDataError,
    is_research_decision_time,
    validate_candles_match_market,
)
from .decisions import ResearchDecisionError, parse_research_trade_calls
from .points import PointCandidate
from .simulator import (
    SimulatedEpisode,
    SimulationError,
    simulate_episode_from_entry,
    simulate_shadow_episode_from_entry,
    with_funding,
)


class ResearchEvaluationError(ValueError):
    """Raised when a saved decision cannot be evaluated safely."""


_REQUEST_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DECISION_KEYS = {
    "side",
    "stop_loss_pct",
    "take_profit_pct",
    "confidence",
    "thesis",
    "would_abstain",
    "abstain_reason",
}
_DECISION_ROW_KEYS = {
    "decision_time_ms",
    "request_id",
    "trial_id",
    "request_hash",
    "requested_model",
    "returned_model",
    "received_at_ms",
    "decision",
}


@dataclass(frozen=True)
class ValidatedPointDecision:
    decision_time_ms: int
    request_id: str
    trial_id: str
    request_hash: str
    requested_model: str
    returned_model: str
    received_at_ms: int
    decision: TradeDecision


@dataclass(frozen=True)
class PointEvaluationOutcome:
    record: ValidatedPointDecision
    status: str
    episode: SimulatedEpisode | None

    @property
    def kind(self) -> str:
        return "shadow" if self.record.decision.would_abstain else "trade"


@dataclass(frozen=True)
class PointEvaluation:
    status: str
    outcomes: tuple[PointEvaluationOutcome, ...]
    funding_status: str

    def public_summary(self) -> dict[str, int | str]:
        complete = [outcome for outcome in self.outcomes if outcome.episode is not None]
        return {
            "status": self.status,
            "decision_count": len(self.outcomes),
            "trade_episodes": sum(outcome.kind == "trade" for outcome in complete),
            "shadow_episodes": sum(outcome.kind == "shadow" for outcome in complete),
            "incomplete_decisions": sum(outcome.episode is None for outcome in self.outcomes),
            "incomplete_price_decisions": sum(
                outcome.status == "incomplete_price" for outcome in self.outcomes
            ),
            "incomplete_funding_decisions": sum(
                outcome.status == "incomplete_funding" for outcome in self.outcomes
            ),
            "funding_status": self.funding_status,
        }


def _string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResearchEvaluationError(f"{name} must be a non-empty string")
    return value


def _timestamp(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchEvaluationError(f"{name} must be a non-negative integer")
    return value


def _parse_decision(value: Any, *, limits) -> TradeDecision:
    if not isinstance(value, dict) or set(value) != _DECISION_KEYS:
        raise ResearchEvaluationError("decision keys are invalid")
    try:
        return parse_research_trade_calls(
            [FunctionCall(name="open_position", args=dict(value))],
            limits=limits,
        )
    except ResearchDecisionError as error:
        raise ResearchEvaluationError("decision violates research limits") from error


def read_validated_decisions_jsonl(
    path: Path,
    *,
    config: ResearchConfig,
) -> list[ValidatedPointDecision]:
    """Read only the bounded output of ``validate-responses``."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ResearchEvaluationError(f"cannot read validated decisions: {path}") from error
    records: list[ValidatedPointDecision] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) != _DECISION_ROW_KEYS:
                raise TypeError("decision row keys are invalid")
            decision_time_ms = _timestamp(row["decision_time_ms"], name="decision_time_ms")
            request_hash = _string(row["request_hash"], name="request_hash")
            trial_id = _string(row["trial_id"], name="trial_id")
            request_id = _string(row["request_id"], name="request_id")
            requested_model = _string(row["requested_model"], name="requested_model")
            returned_model = _string(row["returned_model"], name="returned_model")
            received_at_ms = _timestamp(row["received_at_ms"], name="received_at_ms")
            if not is_research_decision_time(decision_time_ms):
                raise ResearchEvaluationError("decision is off the five-minute decision grid")
            if not _REQUEST_HASH.fullmatch(request_hash):
                raise ResearchEvaluationError("request_hash must be a sha256 hex digest")
            if request_id != f"{trial_id}:{request_hash}":
                raise ResearchEvaluationError("request ID does not match trial and hash")
            if requested_model != config.trader_model or returned_model != requested_model:
                raise ResearchEvaluationError("decision model does not match the research config")
            if received_at_ms < decision_time_ms:
                raise ResearchEvaluationError("decision response precedes its decision time")
            records.append(
                ValidatedPointDecision(
                    decision_time_ms=decision_time_ms,
                    request_id=request_id,
                    trial_id=trial_id,
                    request_hash=request_hash,
                    requested_model=requested_model,
                    returned_model=returned_model,
                    received_at_ms=received_at_ms,
                    decision=_parse_decision(row["decision"], limits=config.decision_limits),
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError, ResearchDecisionError, ResearchEvaluationError) as error:
            raise ResearchEvaluationError(f"invalid validated decision at line {line_number}") from error
    if not records:
        raise ResearchEvaluationError("validated decision artifact is empty")
    if len({record.request_id for record in records}) != len(records):
        raise ResearchEvaluationError("validated decision request IDs must be unique")
    if len({record.decision_time_ms for record in records}) != len(records):
        raise ResearchEvaluationError("validated decision times must be unique")
    return records


def evaluate_validated_decisions(
    *,
    candles: list[NormalizedCandle],
    points: list[PointCandidate],
    decisions: list[ValidatedPointDecision],
    config: ResearchConfig,
    funding: FundingSeries | None = None,
    verify_point_features: bool = False,
) -> PointEvaluation:
    """Independently simulate selected decisions; no virtual account is mutated.

    A normal decision produces a ``trade`` episode.  A ``would_abstain``
    decision produces an identically costed ``shadow`` episode, which is kept
    as reference evidence only and never combined with account equity here.
    """

    if not points:
        raise ResearchEvaluationError("point selection is empty")
    if not decisions:
        raise ResearchEvaluationError("validated decisions are empty")
    if any(
        point.venue != config.market_venue or point.symbol != config.symbol
        for point in points
    ):
        raise ResearchEvaluationError("selected point market does not match the research config")
    point_times = {point.decision_time_ms for point in points}
    decision_times = {record.decision_time_ms for record in decisions}
    if point_times != decision_times:
        raise ResearchEvaluationError("validated decisions must match selected point times exactly")
    if not candles:
        return PointEvaluation(
            status="insufficient_data",
            outcomes=tuple(
                PointEvaluationOutcome(record=record, status="incomplete_price", episode=None)
                for record in sorted(decisions, key=lambda record: record.decision_time_ms)
            ),
            funding_status=(
                "not_required"
                if config.market_venue != BINANCE_USDM_VENUE
                else "not_provided"
                if funding is None
                else "provided"
            ),
        )
    validate_candles_match_market(
        candles, venue=config.market_venue, symbol=config.symbol
    )
    series = CandleSeries(candles)
    funding_required = config.market_venue == BINANCE_USDM_VENUE
    outcomes: list[PointEvaluationOutcome] = []
    for record in sorted(decisions, key=lambda record: record.decision_time_ms):
        if verify_point_features:
            matching_point = next(point for point in points if point.decision_time_ms == record.decision_time_ms)
            try:
                expected_features = series.build_features(decision_time_ms=record.decision_time_ms)
            except ResearchDataError:
                expected_features = None
            if expected_features is not None and expected_features.to_prompt_dict() != matching_point.features.to_prompt_dict():
                raise ResearchEvaluationError("point features do not match the supplied candle artifact")
        try:
            entry_index = series.entry_index(
                decision_time_ms=record.decision_time_ms, config=config.execution
            )
            entry = candles[entry_index]
            quantity = config.reference_notional / Decimal(str(entry.open))
            if record.decision.would_abstain:
                episode = simulate_shadow_episode_from_entry(
                    decision=record.decision,
                    decision_time_ms=record.decision_time_ms,
                    quantity=quantity,
                    candles=candles,
                    entry_index=entry_index,
                    config=config.execution,
                )
            else:
                episode = simulate_episode_from_entry(
                    decision=record.decision,
                    decision_time_ms=record.decision_time_ms,
                    quantity=quantity,
                    candles=candles,
                    entry_index=entry_index,
                    config=config.execution,
                )
        except (ResearchDataError, SimulationError):
            outcomes.append(PointEvaluationOutcome(record=record, status="incomplete_price", episode=None))
            continue
        if funding is None and funding_required:
            outcomes.append(PointEvaluationOutcome(record=record, status="incomplete_funding", episode=None))
            continue
        if funding is not None:
            try:
                episode = with_funding(
                    episode,
                    funding.payment(
                        entry_time_ms=episode.entry_time_ms,
                        exit_time_ms=episode.exit_time_ms,
                        notional=episode.entry_price * episode.quantity,
                        side=episode.side.value,
                    ),
                )
            except FundingDataError:
                outcomes.append(PointEvaluationOutcome(record=record, status="incomplete_funding", episode=None))
                continue
        outcomes.append(PointEvaluationOutcome(record=record, status="complete", episode=episode))
    complete = any(outcome.episode is not None for outcome in outcomes)
    incomplete_funding = any(outcome.status == "incomplete_funding" for outcome in outcomes)
    incomplete = any(outcome.episode is None for outcome in outcomes)
    return PointEvaluation(
        status="partial" if complete and incomplete else "ok" if complete else "insufficient_data",
        outcomes=tuple(outcomes),
        funding_status="not_required"
        if not funding_required
        else "not_provided"
        if funding is None
        else "missing_events"
        if incomplete_funding
        else "provided",
    )


def _episode_row(episode: SimulatedEpisode) -> dict[str, str]:
    return {
        "entry_time_ms": str(episode.entry_time_ms),
        "exit_time_ms": str(episode.exit_time_ms),
        "side": episode.side.value,
        "quantity": format(episode.quantity, "f"),
        "entry_price": format(episode.entry_price, "f"),
        "exit_price": format(episode.exit_price, "f"),
        "exit_reason": episode.exit_reason,
        "gross_pnl": format(episode.gross_pnl, "f"),
        "fee": format(episode.fee, "f"),
        "funding": format(episode.funding, "f"),
        "net_pnl": format(episode.net_pnl, "f"),
        "quality": episode.quality,
    }


def write_point_evaluation_jsonl(path: Path, evaluation: PointEvaluation) -> None:
    """Atomically write complete and incomplete outcomes for later aggregation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for outcome in evaluation.outcomes:
            record = outcome.record
            handle.write(
                json.dumps(
                    {
                        "decision_time_ms": record.decision_time_ms,
                        "request_id": record.request_id,
                        "trial_id": record.trial_id,
                        "request_hash": record.request_hash,
                        "kind": outcome.kind,
                        "status": outcome.status,
                        "episode": _episode_row(outcome.episode) if outcome.episode else None,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
            handle.write("\n")
    temporary.replace(path)
