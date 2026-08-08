import unittest

from base_lp.backtest.models import Observation
from base_lp.strategies.threshold_reset import ThresholdResetStrategy
from base_lp.strategies.static import StaticRangeStrategy


def observation(tick: int, timestamp: int = 100, cost: float = 0.0) -> Observation:
    return Observation(
        block_number=1,
        timestamp=timestamp,
        tick=tick,
        sqrt_price_x96=1 << 96,
        pool_liquidity=1_000_000,
        current_lower_tick=-100,
        current_upper_tick=100,
        last_reset_timestamp=0,
        estimated_rebalance_cost_usd=cost,
        capital_usd=5_000.0,
    )


class StrategyTests(unittest.TestCase):
    def test_static_strategy_holds_even_when_price_leaves_range(self):
        strategy = StaticRangeStrategy(half_width_ticks=100)

        decision = strategy.decide(observation(200))

        self.assertEqual(decision.action, "hold")

    def test_threshold_strategy_resets_when_trigger_and_cost_gate_pass(self):
        strategy = ThresholdResetStrategy(half_width_ticks=100, trigger_ratio=0.8, cooldown_seconds=0)

        decision = strategy.decide(observation(90, cost=0.1))

        self.assertEqual(decision.action, "reset")
        self.assertIn("threshold", decision.reason)

    def test_threshold_strategy_does_nothing_when_cost_is_too_high(self):
        strategy = ThresholdResetStrategy(
            half_width_ticks=100,
            trigger_ratio=0.8,
            cooldown_seconds=0,
            safety_margin_usd=0.01,
        )

        decision = strategy.decide(observation(90, cost=10_000.0))

        self.assertEqual(decision.action, "hold")
        self.assertIn("cost", decision.reason)


if __name__ == "__main__":
    unittest.main()
