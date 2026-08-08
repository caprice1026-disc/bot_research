import unittest

from base_lp.backtest.state import PoolState
from base_lp.schemas import SwapEvent
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
        amount0=1,
        amount1=-1,
        sqrt_price_x96=get_sqrt_ratio_at_tick(tick),
        liquidity=100,
        tick=tick,
    )


class StateTests(unittest.TestCase):
    def test_replay_updates_post_swap_state(self):
        state = PoolState()

        state.replay_swap(event(10, 50))

        self.assertEqual(state.last_key, (10, 0, 0))
        self.assertEqual(state.tick, 50)
        self.assertEqual(state.liquidity, 100)

    def test_replay_rejects_out_of_order_event(self):
        state = PoolState()
        state.replay_swap(event(10, 50))

        with self.assertRaises(ValueError):
            state.replay_swap(event(9, 60))


if __name__ == "__main__":
    unittest.main()
