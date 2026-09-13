"""Sequential, account-aware replay of validated research decisions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .binance import BINANCE_USDM_VENUE, FundingDataError, FundingSeries
from .config import ResearchConfig
from .data import CandleSeries, NormalizedCandle, ResearchDataError, validate_candles_match_market
from .evaluation import ValidatedPointDecision
from .risk import ResearchRiskEngine
from .simulator import SimulatedEpisode, SimulationError, VirtualAccount, simulate_episode_from_entry, simulate_shadow_episode_from_entry, with_funding


@dataclass(frozen=True)
class ReplayResult:
    status: str
    episodes: tuple[SimulatedEpisode, ...]
    events: tuple[dict[str, object], ...]
    initial_equity: Decimal
    final_equity: Decimal


def run_replay(*, candles: list[NormalizedCandle], decisions: list[ValidatedPointDecision], config: ResearchConfig, risk: ResearchRiskEngine, funding: FundingSeries | None = None, days: int = 3) -> ReplayResult:
    if days <= 0:
        raise ValueError("days must be positive")
    if not candles or not decisions:
        return ReplayResult("insufficient_data", (), (), config.initial_equity, config.initial_equity)
    validate_candles_match_market(candles, venue=config.market_venue, symbol=config.symbol)
    series = CandleSeries(candles)
    account = VirtualAccount(config.initial_equity, config.initial_equity)
    episodes: list[SimulatedEpisode] = []
    events: list[dict[str, object]] = []
    position_free_at = 0
    for record in sorted(decisions, key=lambda item: item.decision_time_ms):
        account.advance_time(record.decision_time_ms)
        if record.decision.would_abstain:
            events.append({"decision_time_ms": record.decision_time_ms, "status": "abstained"})
            continue
        try:
            entry_index = series.entry_index(decision_time_ms=record.decision_time_ms, config=config.execution)
            entry = candles[entry_index]
            check = risk.check_entry(account=account, entry_price=Decimal(str(entry.open)), stop_loss_pct=record.decision.stop_loss_pct, position_open=record.decision_time_ms < position_free_at)
            if not check.allowed:
                events.append({"decision_time_ms": record.decision_time_ms, "status": "risk_rejected", "reason": check.reason})
                continue
            episode = simulate_episode_from_entry(decision=record.decision, decision_time_ms=record.decision_time_ms, quantity=check.quantity, candles=candles, entry_index=entry_index, config=config.execution)
            if config.market_venue == BINANCE_USDM_VENUE:
                if funding is None:
                    raise FundingDataError("funding is required")
                episode = with_funding(episode, funding.payment(entry_time_ms=episode.entry_time_ms, exit_time_ms=episode.exit_time_ms, notional=episode.entry_price * episode.quantity, side=episode.side.value))
        except (ResearchDataError, SimulationError, FundingDataError) as error:
            events.append({"decision_time_ms": record.decision_time_ms, "status": "incomplete", "reason": type(error).__name__})
            continue
        position_free_at = episode.exit_time_ms
        account.apply(episode)
        episodes.append(episode)
        events.append({"decision_time_ms": record.decision_time_ms, "status": "trade", "episode_id": len(episodes) - 1})
    status = "ok" if episodes and len(events) == len(decisions) else "partial" if episodes else "insufficient_data"
    return ReplayResult(status, tuple(episodes), tuple(events), config.initial_equity, account.equity)
