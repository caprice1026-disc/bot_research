"""Availability and missing-bar safeguards for the independent spot proxy."""
import unittest

from expanded_book_data import with_binance


class BinanceAvailabilityTests(unittest.TestCase):
    def test_bar_is_not_visible_at_its_available_timestamp(self):
        rows = [{"decision_ts": t} for t in (1, 2, 3)]
        bars = [{"source_ts": 1, "available_ts": 2, "price": 100}]
        result = list(with_binance(rows, bars))
        self.assertNotIn("binance_price", result[0])
        self.assertNotIn("binance_price", result[1])
        self.assertEqual(result[2]["binance_price"], 100)
        self.assertEqual(result[2]["binance_age_seconds"], 1)
        self.assertNotIn("binance_bid", result[2])
        self.assertNotIn("chainlink60_price", result[2])

    def test_twap_needs_sixty_contiguous_observed_seconds(self):
        bars = [{"source_ts": t, "available_ts": t + 1, "price": t} for t in range(1, 61)]
        row = next(with_binance([{"decision_ts": 62}], bars))
        self.assertEqual(row["binance_twap60_price"], 30.5)
        missing = bars[:20] + bars[21:] + [{"source_ts": 61, "available_ts": 62, "price": 61}]
        row = next(with_binance([{"decision_ts": 63}], missing))
        self.assertIsNone(row["binance_twap60_price"])


if __name__ == "__main__":
    unittest.main()
