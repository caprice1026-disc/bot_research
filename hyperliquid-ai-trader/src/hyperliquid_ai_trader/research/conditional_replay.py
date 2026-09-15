"""Pending-exit, account-aware replay for fixed conditional candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Callable, Mapping

from .binance import BINANCE_USDM_VENUE, FundingDataError, FundingSeries
from .conditional_edge import CandidateSpec, ConditionalStudyConfig, candidate_decision, derive_regime_features
from .data import CandleSeries, NormalizedCandle, ResearchDataError, validate_candles_match_market
from .risk import ResearchRiskEngine
from .simulator import SimulatedEpisode, SimulationError, VirtualAccount, simulate_episode_from_entry, with_funding


@dataclass(frozen=True)
class CandidateReplayResult:
    status: str
    candidate: CandidateSpec
    episodes: tuple[SimulatedEpisode, ...]
    decisions: tuple[dict[str, object], ...]
    initial_equity: Decimal
    final_equity: Decimal


def run_candidate_replay(
    *,
    candles: list[NormalizedCandle],
    funding: FundingSeries | None,
    study_config: ConditionalStudyConfig,
    candidate: CandidateSpec,
    thresholds: Mapping[str, Mapping[Decimal, Decimal]],
    thresholds_for_time: Callable[[int], Mapping[str, Mapping[Decimal, Decimal]]] | None = None,
) -> CandidateReplayResult:
    """Replay one candidate without letting its future exit alter an open account."""

    validate_candles_match_market(
        candles,
        venue=study_config.base_config.market_venue,
        symbol=study_config.base_config.symbol,
    )
    series = CandleSeries(candles)
    account = VirtualAccount(
        equity=study_config.base_config.initial_equity,
        day_start_equity=study_config.base_config.initial_equity,
    )
    risk = ResearchRiskEngine(
        risk_per_trade_pct=study_config.base_config.risk_per_trade_pct,
        max_daily_loss_pct=study_config.base_config.max_daily_loss_pct,
        max_drawdown_pct=study_config.base_config.max_drawdown_pct,
        max_position_notional_usd=study_config.base_config.max_position_notional_usd,
        leverage=study_config.base_config.leverage,
        min_notional_usd=study_config.base_config.min_notional_usd,
    )
    execution = replace(
        study_config.base_config.execution,
        max_hold_ms=candidate.hold_ms,
        exit_policy=candidate.exit_profile,
    )
    pending: SimulatedEpisode | None = None
    episodes: list[SimulatedEpisode] = []
    decisions: list[dict[str, object]] = []
    account_partial = False

    for decision_time_ms in range(
        study_config.start_ms, study_config.end_ms, 300_000
    ):
        if pending is not None and pending.exit_time_ms <= decision_time_ms:
            account.apply(pending)
            episodes.append(pending)
            pending = None
        account.advance_time(decision_time_ms)
        equity_before = account.equity
        base_row = {
            "candidate_id": candidate.candidate_id,
            "decision_time_ms": decision_time_ms,
            "equity_before": _decimal(equity_before),
        }
        if account_partial:
            decisions.append({**base_row, "status": "incomplete", "reason": "account_partial"})
            continue
        try:
            features = series.build_features(decision_time_ms=decision_time_ms)
        except ResearchDataError as error:
            decisions.append({**base_row, "status": "incomplete", "reason": str(error)})
            continue
        if pending is not None:
            decisions.append({**base_row, "status": "position_blocked"})
            continue
        feature_row = asdict(features)
        decision = candidate_decision(
            candidate,
            feature_row,
            thresholds_for_time(decision_time_ms) if thresholds_for_time is not None else thresholds,
        )
        if decision.would_abstain:
            decisions.append({**base_row, "status": "signal_abstain"})
            continue
        try:
            entry_index = series.entry_index(
                decision_time_ms=decision_time_ms,
                config=execution,
            )
            entry = candles[entry_index]
            check = risk.check_entry(
                account=account,
                entry_price=Decimal(str(entry.open)),
                stop_loss_pct=decision.stop_loss_pct,
                position_open=False,
            )
            if not check.allowed:
                decisions.append(
                    {**base_row, "status": "risk_rejected", "reason": check.reason}
                )
                continue
            episode = simulate_episode_from_entry(
                decision=decision,
                decision_time_ms=decision_time_ms,
                quantity=check.quantity,
                candles=candles,
                entry_index=entry_index,
                config=execution,
            )
            if study_config.base_config.market_venue == BINANCE_USDM_VENUE:
                if funding is None:
                    raise FundingDataError("funding is required")
                episode = with_funding(
                    episode,
                    funding.payment(
                        entry_time_ms=episode.entry_time_ms,
                        exit_time_ms=episode.exit_time_ms,
                        notional=episode.entry_price * episode.quantity,
                        side=episode.side.value,
                    ),
                )
        except FundingDataError as error:
            account_partial = True
            decisions.append({**base_row, "status": "incomplete", "reason": str(error)})
            continue
        except (ResearchDataError, SimulationError) as error:
            decisions.append({**base_row, "status": "incomplete", "reason": str(error)})
            continue
        pending = episode
        decisions.append(
            {
                **base_row,
                "status": "executed",
                "side": episode.side.value,
                "alignment": derive_regime_features(
                    feature_row, study_config.base_config.execution
                )["alignment"],
                "entry_time_ms": episode.entry_time_ms,
                "exit_time_ms": episode.exit_time_ms,
            }
        )
    if pending is not None:
        account.apply(pending)
        episodes.append(pending)
    incomplete = any(row["status"] == "incomplete" for row in decisions)
    status = "ok" if episodes and not incomplete else "partial" if episodes else "insufficient_data"
    return CandidateReplayResult(
        status=status,
        candidate=candidate,
        episodes=tuple(episodes),
        decisions=tuple(decisions),
        initial_equity=study_config.base_config.initial_equity,
        final_equity=account.equity,
    )


def _decimal(value: Decimal) -> str:
    return format(value, "f")
