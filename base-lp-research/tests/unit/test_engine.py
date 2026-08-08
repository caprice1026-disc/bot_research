import unittest

from base_lp.backtest.engine import SimulationConfig, run_backtest
from base_lp.schemas import SwapEvent
from base_lp.strategies.static import StaticRangeStrategy
from base_lp.uniswap_v3.tick_math import get_sqrt_ratio_at_tick


def swap(block: int, tick: int, amount0: int, amount1: int) -> SwapEvent:
    return SwapEvent(
        block_number=block,
        transaction_index=0,
        log_index=0,
        block_hash=f"0x{block:x}",
        transaction_hash=f"0xtx{block:x}",
        address="0x" + "ab" * 20,
        timestamp=block * 2,
        sender="0x" + "11" * 20,
        recipient="0x" + "22" * 20,
        amount0=amount0,
        amount1=amount1,
        sqrt_price_x96=get_sqrt_ratio_at_tick(tick),
        liquidity=10**9,
        tick=tick,
    )


class EngineTests(unittest.TestCase):
    def test_replays_swaps_and_records_fee_and_equity(self):
        events = [swap(1, 0, -10_000, 10_000), swap(2, 50, 1_000, -1_000)]
        result = run_backtest(
            events,
            StaticRangeStrategy(half_width_ticks=100),
            SimulationConfig(
                capital_usd=5_000,
                token0_decimals=0,
                token1_decimals=0,
                fee_tier=500,
                gas_used=200_000,
                gas_price_wei=1_000_000,
            ),
        )

        self.assertEqual(len(result.equity_curve), 2)
        self.assertGreater(result.total_fees_usd, 0)
        self.assertGreater(result.terminal_value_usd, 0)
        self.assertEqual(result.action_log[0]["action"], "hold")


if __name__ == "__main__":
    unittest.main()
