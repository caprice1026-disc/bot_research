import unittest

from base_lp.backtest.benchmarks import hodl_terminal_value


class BenchmarkTests(unittest.TestCase):
    def test_hodl_value_uses_initial_token_quantities_and_final_price(self):
        self.assertEqual(hodl_terminal_value(2.0, 100.0, 3.0), 106.0)


if __name__ == "__main__":
    unittest.main()
