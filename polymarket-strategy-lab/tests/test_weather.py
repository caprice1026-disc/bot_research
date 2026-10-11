"""Regression checks for the weather study's temporal and execution boundaries."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_backtest import bucket_probability, dead_bucket, execution_proxy, fee_per_share, last_price, parse_bucket, released_max
from weather_data import settlement_source


class WeatherBoundaries(unittest.TestCase):
    def setUp(self):
        self.cfg = {"execution_latency_seconds": 60, "execution_expiry_seconds": 900,
                    "max_price_move": .05, "order_shares": 5, "tape_participation_cap": .1,
                    "current_taker_fee_rate": .05}

    def trade(self, ts, price=.4, size=100, side="BUY"):
        return {"timestamp": ts, "price": price, "size": size, "side": side, "transaction_hash": str(ts)}

    def test_bucket_partition_and_rejection(self):
        bounds = [parse_bucket("63°F or below"), parse_bucket("64-65°F"), parse_bucket("66°F or higher")]
        self.assertAlmostEqual(sum(bucket_probability(65, 2, b) for b in bounds), 1)
        self.assertEqual(bounds[0], (-math.inf, 63))
        with self.assertRaises(ValueError):
            parse_bucket("exactly 65 maybe")

    def test_signal_strictly_precedes_decision_and_is_fresh(self):
        rows = [self.trade(100), self.trade(200, .9)]
        self.assertEqual(last_price(rows, 200, 101)["price"], .4)
        self.assertIsNone(last_price(rows, 200, 99))
        self.assertIsNone(last_price(rows, 100, 100))

    def test_observation_release_lag_and_dead_bucket_margin(self):
        obs = [{"timestamp": 100, "fahrenheit": 80}, {"timestamp": 1900, "fahrenheit": 95}]
        self.assertEqual(released_max(obs, 1900, 30), 80)
        self.assertFalse(dead_bucket((78, 79), 80, 2))
        self.assertTrue(dead_bucket((78, 79), 81, 2))
        self.assertFalse(dead_bucket((80, math.inf), 100, 2))

    def test_first_future_buy_does_not_cherry_pick_size(self):
        rows = [self.trade(1000), self.trade(1060, size=5), self.trade(1070, size=500)]
        fill, reason = execution_proxy(rows, 1000, .4, self.cfg, .01)
        self.assertIsNone(fill)
        self.assertEqual(reason, "insufficient_print_size")

    def test_execution_latency_fees_and_price_limit(self):
        rows = [self.trade(1000, .1), self.trade(1059, .2), self.trade(1060, .4)]
        fill, reason = execution_proxy(rows, 1000, .4, self.cfg, .01)
        self.assertIsNone(reason)
        self.assertEqual(fill["entry_price"], .41)
        self.assertEqual(fill["fill_timestamp"], 1060)
        self.assertEqual(fill["fees"], .06048)
        self.assertAlmostEqual(fill["cost"], 5 * .41 + .06048)
        self.assertAlmostEqual(fee_per_share(.5, .05), .0125)
        self.assertEqual(execution_proxy([self.trade(1060, .5)], 1000, .4, self.cfg, .01)[1], "price_limit")
        self.assertEqual(execution_proxy([self.trade(1901)], 1000, .4, self.cfg, .01)[1], "execution_timeout")

    def test_market_rule_station_guard_and_provider_change(self):
        market = {"description": "Chicago O'Hare Intl Airport Station whole degrees Fahrenheit Show Hourly Data",
                  "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=kord"}
        self.assertEqual(settlement_source(market), "NOAA_KORD_hourly")
        market["resolutionSource"] = "https://www.weather.gov/wrh/timeseries?site=klga"
        with self.assertRaises(ValueError):
            settlement_source(market)


if __name__ == "__main__":
    unittest.main()
