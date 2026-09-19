from decimal import Decimal

from llm_position_management.comparison import ControlledPolicy, feature_reader
from llm_position_management.policy import ScriptedPolicy
from trading_core.market_data.models import MarketTick


def test_features_are_unaffected_by_future_candles():
    ticks = [MarketTick(i*60_000,Decimal(100+i),Decimal(100+i),Decimal(100+i),Decimal(100+i)) for i in range(70)]
    before = feature_reader(ticks)(60*60_000)
    ticks[-1] = MarketTick(69*60_000,Decimal(9999),Decimal(9999),Decimal(9999),Decimal(9999))
    assert feature_reader(ticks)(60*60_000) == before


def test_terminal_administrative_noop_does_not_wait_past_dataset():
    policy = ControlledPolicy(ScriptedPolicy({}),end=600_000)
    response = policy.decide({"as_of_ms":600_000},"terminal")
    assert response.received_at_ms == 600_000
    assert response.payload["target_fraction"] == "0"
