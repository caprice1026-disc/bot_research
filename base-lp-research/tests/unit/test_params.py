import unittest

from base_lp.strategies.params import half_width_ticks_for_pct


class StrategyParamsTests(unittest.TestCase):
    def test_half_width_percentage_rounds_up_to_tick_spacing(self):
        self.assertEqual(half_width_ticks_for_pct(0.5, tick_spacing=10), 50)
        self.assertEqual(half_width_ticks_for_pct(0.1, tick_spacing=10), 10)

    def test_rejects_non_positive_width(self):
        with self.assertRaises(ValueError):
            half_width_ticks_for_pct(0.0, tick_spacing=10)


if __name__ == "__main__":
    unittest.main()
