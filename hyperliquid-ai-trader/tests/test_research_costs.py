from decimal import Decimal

from hyperliquid_ai_trader.models import Side, TradeDecision
from hyperliquid_ai_trader.research.binance import FundingEvent, FundingSeries
from hyperliquid_ai_trader.research.costs import estimated_round_trip_cost_bps, execution_price
from hyperliquid_ai_trader.research.simulator import ExecutionConfig, simulate_episode_from_entry
from hyperliquid_ai_trader.research.data import NormalizedCandle


def _decision(side: Side = Side.LONG) -> TradeDecision:
    return TradeDecision(side, Decimal("1"), Decimal("1"), Decimal("0.5"), "fixture", False, None)


def _candles() -> list[NormalizedCandle]:
    return [
        NormalizedCandle("hyperliquid_mainnet_public", "BTC", i * 60_000, (i + 1) * 60_000, 100, 100.2, 99.2, 100, 1, (i + 1) * 60_000, (i + 1) * 60_000, "fixture")
        for i in range(8)
    ]


def test_stop_and_take_are_based_on_execution_entry_price() -> None:
    episode = simulate_episode_from_entry(
        decision=_decision(), decision_time_ms=0, quantity=Decimal("1"), candles=_candles(), entry_index=0,
        config=ExecutionConfig(model_delay_ms=0, max_arrival_delay_ms=0, max_hold_ms=60_000, spread_bps=100, slippage_bps=0, fee_rate=Decimal("0")),
    )
    assert episode.exit_reason == "stop_loss"
    assert episode.entry_price == Decimal("100.5")


def test_funding_at_entry_boundary_does_not_charge_previous_slot() -> None:
    funding = FundingSeries((FundingEvent(8 * 60 * 60 * 1_000, Decimal("0.001")),))
    assert funding.payment(entry_time_ms=8 * 60 * 60 * 1_000, exit_time_ms=8 * 60 * 60 * 1_000 + 1, notional=Decimal("100"), side="long") == Decimal("0")


def test_cost_estimate_uses_same_execution_parameters() -> None:
    config = ExecutionConfig(fee_rate=Decimal("0.001"), spread_bps=Decimal("2"), slippage_bps=Decimal("1"))
    assert estimated_round_trip_cost_bps(config) == Decimal("24")
    assert execution_price(Decimal("100"), side=Side.LONG, entering=True, config=config) == Decimal("100.02")
