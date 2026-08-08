import unittest

from base_lp.uniswap_v3.liquidity_math import (
    amounts_for_liquidity,
    liquidity_for_amounts,
)


class LiquidityMathTests(unittest.TestCase):
    def test_round_trip_inside_range_is_close_without_float_math(self):
        lower = 1 << 96
        current = 2 << 96
        upper = 3 << 96
        liquidity = 1_000_000

        amount0, amount1 = amounts_for_liquidity(current, lower, upper, liquidity)
        recovered = liquidity_for_amounts(current, lower, upper, amount0, amount1)

        self.assertGreater(amount0, 0)
        self.assertGreater(amount1, 0)
        self.assertGreaterEqual(recovered, liquidity - 8)
        self.assertLessEqual(recovered, liquidity)

    def test_outside_range_has_one_token_only(self):
        amount0, amount1 = amounts_for_liquidity(4 << 96, 1 << 96, 3 << 96, 1000)

        self.assertEqual(amount0, 0)
        self.assertGreater(amount1, 0)


if __name__ == "__main__":
    unittest.main()
