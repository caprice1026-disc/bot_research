import unittest

from base_lp.uniswap_v3.tick_math import (
    MAX_SQRT_RATIO,
    MAX_TICK,
    MIN_SQRT_RATIO,
    MIN_TICK,
    get_sqrt_ratio_at_tick,
    get_tick_at_sqrt_ratio,
)


class TickMathTests(unittest.TestCase):
    def test_known_boundary_vectors_match_uniswap_v3(self):
        self.assertEqual(get_sqrt_ratio_at_tick(0), 1 << 96)
        self.assertEqual(get_sqrt_ratio_at_tick(MIN_TICK), MIN_SQRT_RATIO)
        self.assertEqual(get_sqrt_ratio_at_tick(MAX_TICK), MAX_SQRT_RATIO)

    def test_inverse_respects_tick_interval_boundaries(self):
        self.assertEqual(get_tick_at_sqrt_ratio(MIN_SQRT_RATIO), MIN_TICK)
        self.assertEqual(get_tick_at_sqrt_ratio(1 << 96), 0)
        self.assertEqual(get_tick_at_sqrt_ratio(MAX_SQRT_RATIO - 1), MAX_TICK - 1)

    def test_rejects_out_of_domain_inputs(self):
        with self.assertRaises(ValueError):
            get_sqrt_ratio_at_tick(MIN_TICK - 1)
        with self.assertRaises(ValueError):
            get_tick_at_sqrt_ratio(MAX_SQRT_RATIO)


if __name__ == "__main__":
    unittest.main()
