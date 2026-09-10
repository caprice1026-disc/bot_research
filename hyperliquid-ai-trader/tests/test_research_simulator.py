from __future__ import annotations

from decimal import Decimal

import pytest

from hyperliquid_ai_trader.models import Side, TradeDecision
from hyperliquid_ai_trader.research.data import NormalizedCandle
from hyperliquid_ai_trader.research.simulator import (
    ExecutionConfig,
    IntrabarPolicy,
    SimulationError,
    VirtualAccount,
    baseline_decision,
    simulate_episode,
)


def _decision(side: Side = Side.LONG) -> TradeDecision:
    return TradeDecision(
        side=side,
        stop_loss_pct=Decimal("1"),
        take_profit_pct=Decimal("1"),
        confidence=Decimal("0.5"),
        thesis="fixture",
        would_abstain=False,
        abstain_reason=None,
    )


def _candle(index: int, *, open_: float = 100, high: float = 100.5,
            low: float = 99.5, close: float = 100) -> NormalizedCandle:
    start = index * 60_000
    return NormalizedCandle(
        venue="hyperliquid", symbol="BTC", open_time_ms=start,
        close_exclusive_ms=start + 60_000, open=open_, high=high, low=low,
        close=close, volume=1, received_at_ms=start + 60_000,
        available_at_ms=start + 60_000, availability_kind="fixture",
    )


def test_episode_uses_entry_based_hold_deadline_and_exact_pnl() -> None:
    candles = [_candle(index) for index in range(7)]
    candles[6] = _candle(6, open_=101, high=101, low=101, close=101)
    result = simulate_episode(
        decision=_decision(), decision_time_ms=0, quantity=Decimal("1"), candles=candles,
        config=ExecutionConfig(fee_rate=Decimal("0"), spread_bps=Decimal("0"),
                               slippage_bps=Decimal("0")),
    )
    assert result.entry_time_ms == 60_000
    assert result.exit_time_ms == 360_000
    assert result.exit_reason == "max_hold"
    assert result.gross_pnl == result.net_pnl == Decimal("1")


def test_entry_honors_configured_arrival_allowance_beyond_one_minute() -> None:
    candles = [_candle(index) for index in range(2, 8)]
    result = simulate_episode(
        decision=_decision(),
        decision_time_ms=0,
        quantity=Decimal("1"),
        candles=candles,
        config=ExecutionConfig(
            model_delay_ms=0,
            max_arrival_delay_ms=120_000,
            fee_rate=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
    )

    assert result.entry_time_ms == 120_000
    assert result.exit_time_ms == 420_000


def test_one_minute_simulator_rejects_partial_minute_holding_windows() -> None:
    with pytest.raises(SimulationError, match="whole minutes"):
        ExecutionConfig(max_hold_ms=90_000)


def test_intrabar_collision_is_explicit_and_policy_selects_result() -> None:
    candles = [_candle(0), _candle(1, high=102, low=98)]
    stop = simulate_episode(
        decision=_decision(), decision_time_ms=0, quantity=Decimal("1"), candles=candles,
        config=ExecutionConfig(intrabar_policy=IntrabarPolicy.STOP_FIRST),
    )
    take = simulate_episode(
        decision=_decision(), decision_time_ms=0, quantity=Decimal("1"), candles=candles,
        config=ExecutionConfig(intrabar_policy=IntrabarPolicy.TAKE_FIRST),
    )
    assert stop.quality == take.quality == "ambiguous_intrabar"
    assert stop.exit_reason == "stop_loss"
    assert take.exit_reason == "take_profit"
    assert stop.net_pnl < take.net_pnl


def test_abstention_never_becomes_account_profit() -> None:
    decision = _decision()
    abstention = TradeDecision(**{**decision.__dict__, "would_abstain": True,
                                  "abstain_reason": "条件不足"})
    with pytest.raises(SimulationError, match="not an account episode"):
        simulate_episode(decision=abstention, decision_time_ms=0,
                         quantity=Decimal("1"), candles=[_candle(0), _candle(1)])


def test_baselines_and_shadow_account_are_separated() -> None:
    assert baseline_decision("momentum", return_5m=-0.1).side is Side.SHORT
    assert baseline_decision("mean_reversion", return_5m=-0.1).side is Side.LONG
    assert baseline_decision("always_abstain", return_5m=1).would_abstain

    candles = [_candle(index) for index in range(7)]
    candles[6] = _candle(6, open_=101, high=101, low=101, close=101)
    episode = simulate_episode(
        decision=_decision(), decision_time_ms=0, quantity=Decimal("1"), candles=candles,
        config=ExecutionConfig(fee_rate=Decimal("0"), spread_bps=Decimal("0"),
                               slippage_bps=Decimal("0")),
    )
    account = VirtualAccount(equity=Decimal("1000"), day_start_equity=Decimal("1000"))
    account.apply(episode, kind="shadow")
    assert account.equity == Decimal("1000")
    account.apply(episode)
    assert account.equity == Decimal("1001")
    assert account.daily_realized_pnl == Decimal("1")
