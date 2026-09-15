"""Offline, sequential replay for the three pre-registered simple rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .binance import BINANCE_USDM_VENUE, FundingDataError, FundingSeries
from .data import (
    CandleSeries,
    NormalizedCandle,
    ResearchDataError,
    is_research_decision_time,
    validate_candles_match_market,
)
from .risk import ResearchRiskEngine
from .simulator import (
    ExecutionConfig,
    SimulatedEpisode,
    SimulationError,
    VirtualAccount,
    baseline_decision,
    simulate_episode_from_entry,
    with_funding,
)


@dataclass(frozen=True)
class BaselineReplay:
    """A replay summary that does not call a model or mutate a live account."""

    status: str
    baseline_name: str
    decisions: int
    abstentions: int
    position_blocked: int
    risk_rejected: int
    incomplete_decisions: int
    incomplete_price_decisions: int
    incomplete_funding_decisions: int
    funding_status: str
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
            "risk_rejected": self.risk_rejected,
            "incomplete_decisions": self.incomplete_decisions,
            "incomplete_price_decisions": self.incomplete_price_decisions,
            "incomplete_funding_decisions": self.incomplete_funding_decisions,
            "funding_status": self.funding_status,
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
    risk: ResearchRiskEngine,
    market_venue: str,
    symbol: str,
    funding: FundingSeries | None = None,
) -> BaselineReplay:
    """Replay one fixed rule in chronological order with no overlapping account trade.

    A suffix without sufficient post-decision candles is recorded as incomplete
    rather than being interpreted as a losing or zero-return trade.
    """

    if initial_equity <= 0:
        raise SimulationError("initial equity must be positive")
    if not candles or len(candles) < 61:
        return BaselineReplay(
            status="insufficient_data",
            baseline_name=baseline_name,
            decisions=0,
            abstentions=0,
            position_blocked=0,
            risk_rejected=0,
            incomplete_decisions=0,
            incomplete_price_decisions=0,
            incomplete_funding_decisions=0,
            funding_status=(
                "not_required"
                if market_venue != BINANCE_USDM_VENUE
                else "not_provided"
                if funding is None
                else "provided"
            ),
            episodes=(),
            initial_equity=initial_equity,
            final_equity=initial_equity,
        )
    validate_candles_match_market(candles, venue=market_venue, symbol=symbol)
    series = CandleSeries(candles)
    funding_required = market_venue == BINANCE_USDM_VENUE

    account = VirtualAccount(equity=initial_equity, day_start_equity=initial_equity)
    episodes: list[SimulatedEpisode] = []
    decisions = abstentions = position_blocked = risk_rejected = incomplete_price = incomplete_funding = 0
    position_free_at_ms = 0

    for candle in candles[60:]:
        decision_time_ms = candle.close_exclusive_ms
        if not is_research_decision_time(decision_time_ms):
            continue
        try:
            features = series.build_features(decision_time_ms=decision_time_ms)
        except ResearchDataError:
            incomplete_price += 1
            continue
        decision = baseline_decision(baseline_name, return_5m=features.return_5m)
        decisions += 1
        account.advance_time(decision_time_ms)
        if decision.would_abstain:
            abstentions += 1
            continue
        try:
            entry_index = series.entry_index(
                decision_time_ms=decision_time_ms, config=execution_config
            )
            entry_candle = candles[entry_index]
            check = risk.check_entry(
                account=account,
                entry_price=Decimal(str(entry_candle.open)),
                stop_loss_pct=decision.stop_loss_pct,
                position_open=decision_time_ms < position_free_at_ms,
            )
            if not check.allowed:
                if check.reason == "position_open":
                    position_blocked += 1
                else:
                    risk_rejected += 1
                continue
            episode = simulate_episode_from_entry(
                decision=decision,
                decision_time_ms=decision_time_ms,
                quantity=check.quantity,
                candles=candles,
                entry_index=entry_index,
                config=execution_config,
            )
        except (ResearchDataError, SimulationError):
            incomplete_price += 1
            continue
        position_free_at_ms = episode.exit_time_ms
        if funding is None and funding_required:
            incomplete_funding += 1
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
                incomplete_funding += 1
                continue
        account.apply(episode)
        episodes.append(episode)

    incomplete = incomplete_price + incomplete_funding
    if incomplete:
        status = "partial" if episodes else "insufficient_data"
    else:
        status = "ok"
    funding_status = (
        "not_required"
        if not funding_required
        else "not_provided"
        if funding is None
        else "missing_events"
        if incomplete_funding
        else "provided"
    )

    return BaselineReplay(
        status=status,
        baseline_name=baseline_name,
        decisions=decisions,
        abstentions=abstentions,
        position_blocked=position_blocked,
        risk_rejected=risk_rejected,
        incomplete_decisions=incomplete,
        incomplete_price_decisions=incomplete_price,
        incomplete_funding_decisions=incomplete_funding,
        funding_status=funding_status,
        episodes=tuple(episodes),
        initial_equity=initial_equity,
        final_equity=account.equity,
    )
