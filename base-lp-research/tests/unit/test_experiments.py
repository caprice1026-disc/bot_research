import unittest

from base_lp.backtest.engine import SimulationConfig
from base_lp.experiments.runner import run_parameter_sweep
from base_lp.experiments.walk_forward import assess_walk_forward
from base_lp.schemas import SwapEvent
from base_lp.strategies.static import StaticRangeStrategy
from base_lp.uniswap_v3.tick_math import get_sqrt_ratio_at_tick


def event(block: int, tick: int) -> SwapEvent:
    return SwapEvent(
        block_number=block,
        transaction_index=0,
        log_index=0,
        block_hash=f"0x{block:x}",
        transaction_hash=f"0xtx{block:x}",
        address="0x" + "ab" * 20,
        timestamp=block,
        sender="0x" + "11" * 20,
        recipient="0x" + "22" * 20,
        amount0=-1000,
        amount1=1000,
        sqrt_price_x96=get_sqrt_ratio_at_tick(tick),
        liquidity=10**9,
        tick=tick,
    )


class ExperimentTests(unittest.TestCase):
    def test_parameter_sweep_is_reproducible_and_keeps_config(self):
        rows = run_parameter_sweep(
            [event(1, 0), event(2, 5)],
            [{"half_width_ticks": 100}, {"half_width_ticks": 200}],
            lambda params: StaticRangeStrategy(params["half_width_ticks"]),
            SimulationConfig(5_000, 0, 0, 500),
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["config"]["half_width_ticks"], 100)
        self.assertEqual(len(rows[0]["config_hash"]), 64)

    def test_walk_forward_rejects_short_window(self):
        result = assess_walk_forward([event(1, 0), event(2, 5)])

        self.assertEqual(result["status"], "insufficient_data")
        self.assertIn("9 months", result["reason"])


if __name__ == "__main__":
    unittest.main()
