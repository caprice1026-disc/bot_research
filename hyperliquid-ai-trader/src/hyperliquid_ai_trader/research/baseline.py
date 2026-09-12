"""Offline, sequential replay for the three pre-registered simple rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .data import (
    NormalizedCandle,
    ResearchDataError,
    build_common_candle_features,
    is_research_decision_time,
    validate_contiguous_candles,
)
from .simulator import (
    ExecutionConfig,
    SimulatedEpisode,
    SimulationError,
    VirtualAccount,
    baseline_decision,
    find_entry_candle,
    simulate_episode,
)


@dataclass(frozen=True)
class BaselineReplay:
    """A replay summary that does not call a model or mutate a live account."""

    status: str
    baseline_name: str
    decisions: int
    abstentions: int
    position_blocked: int
    incomplete_decisions: int
    episodes: tuple[SimulatedEpisode, ...]
    initial_equity: Decimal
    final_equity: Decimal

    @property
    def net_pnl(self) -> Decimal:
        return self.final_equity - self.initial_equity

    def public_summary(self) -> dict[str, int | str]:
        return {
            "status": self.status,
            "baseline": self.baseline_name,
            "decisions": self.decisions,
            "abstentions": self.abstentions,
            "position_blocked": self.position_blocked,
            "incomplete_decisions": self.incomplete_decisions,
            "episodes": len(self.episodes),
            "initial_equity": format(self.initial_equity, "f"),
            "final_equity": format(self.final_equity, "f"),
            "net_pnl": format(self.net_pnl, "f"),
        }


def run_baseline(
    *,
    candles: list[NormalizedCandle],
    baseline_name: str,
    execution_config: ExecutionConfig,
    initial_equity: Decimal,
    reference_notional: Decimal,
) -> BaselineReplay:
    """Replay one fixed rule in chronological order with no overlapping account trade.

    A suffix without sufficient post-decision candles is recorded as incomplete
    rather than being interpreted as a losing or zero-return trade.
    """

    if initial_equity <= 0 or reference_notional <= 0:
        raise SimulationError("initial equity and reference notional must be positive")
    if not candles or len(candles) < 61:
        return BaselineReplay(
            status="insufficient_data",
            baseline_name=baseline_name,
            decisions=0,
            abstentions=0,
            position_blocked=0,
            incomplete_decisions=0,
            episodes=(),
            initial_equity=initial_equity,
            final_equity=initial_equity,
        )
    validate_contiguous_candles(candles)

    account = VirtualAccount(equity=initial_equity, day_start_equity=initial_equity)
    episodes: list[SimulatedEpisode] = []
    decisions = abstentions = position_blocked = incomplete = 0
    position_free_at_ms = 0

    for candle in candles[60:]:
        decision_time_ms = candle.close_exclusive_ms
        if not is_research_decision_time(decision_time_ms):
            continue
        try:
            features = build_common_candle_features(
                candles=candles,
                decision_time_ms=decision_time_ms,
            )
        except ResearchDataError:
            incomplete += 1
            continue
        decision = baseline_decision(baseline_name, return_5m=features.return_5m)
        decisions += 1
        if decision.would_abstain:
            abstentions += 1
            continue
        if decision_time_ms < position_free_at_ms:
            position_blocked += 1
            continue
        try:
            entry_candle = find_entry_candle(
                candles=candles,
                decision_time_ms=decision_time_ms,
                config=execution_config,
            )
            quantity = reference_notional / Decimal(str(entry_candle.open))
            episode = simulate_episode(
                decision=decision,
                decision_time_ms=decision_time_ms,
                quantity=quantity,
                candles=candles,
                config=execution_config,
            )
        except SimulationError:
            incomplete += 1
            continue
        account.apply(episode)
        episodes.append(episode)
        position_free_at_ms = episode.exit_time_ms

    return BaselineReplay(
        status="ok" if episodes or not incomplete else "insufficient_data",
        baseline_name=baseline_name,
        decisions=decisions,
        abstentions=abstentions,
        position_blocked=position_blocked,
        incomplete_decisions=incomplete,
        episodes=tuple(episodes),
        initial_equity=initial_equity,
        final_equity=account.equity,
    )
