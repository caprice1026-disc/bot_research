"""Independent causal and execution-assumption checks for weather research."""
from datetime import date, timedelta
import json
import math
from pathlib import Path
import unittest

from weather_backtest import calibrate, execution_proxy, last_price, run_strategy, summarize, utc_decision


CONFIG = json.loads((Path(__file__).resolve().parents[1] / "research/weather_config.json").read_text())


def trade(ts, price=.5, *, side="BUY", size=100, transaction="trade"):
    return {"timestamp": ts, "price": price, "side": side,
            "size": size, "transaction_hash": transaction}


def sample_day(cfg):
    decision = utc_decision("2026-09-01", 10, cfg)
    market = {"condition_id": "bucket", "title": "80°F or below", "bounds": (-math.inf, 80),
        "yes_settlement": 0, "resolution_timestamp": decision + 86400,
        "close_timestamp": decision + 86400, "settlement_source": "NOAA_KORD_hourly",
        "resolution_transaction_hash": "resolution", "trades": {"Yes": [], "No": [
            trade(decision - 600, transaction="signal"), trade(decision + 60, transaction="fill")]}}
    return {"date": "2026-09-01", "model_maxes": {m: 85 for m in cfg["forecast_models"]},
            "observations": [{"timestamp": decision - 3600, "fahrenheit": 85}],
            "markets": [market]}, decision


class IndependentWeatherReview(unittest.TestCase):
    def test_fee_precision_is_five_decimals(self):
        fill, reason = execution_proxy([trade(160, .333)], 100, .333, CONFIG, 0)
        self.assertIsNone(reason)
        self.assertEqual(fill["fees"], .05553)

    def test_unfillable_first_buy_cannot_search_later_favorable_trade(self):
        prints = [trade(150, .4, side="SELL"), trade(160, .9), trade(170, .4)]
        fill, reason = execution_proxy(prints, 100, .4, CONFIG, 0)
        self.assertIsNone(fill)
        self.assertEqual(reason, "price_limit")

    def test_signal_price_strictly_precedes_decision(self):
        prior = trade(99, .4)
        self.assertEqual(last_price([prior, trade(100, .1)], 100, 1000), prior)

    def test_future_observation_cannot_create_earlier_dead_bucket_signal(self):
        cfg = dict(CONFIG, observed_decision_local_hours=[10])
        fit = {"weights": {cfg["forecast_models"][0]: 1}, "bias_f": 0, "sigma_f": 2}
        day, decision = sample_day(cfg)
        baseline_fills, _, _ = run_strategy([day], cfg, fit, "observed_dead_bucket_no", 0)
        self.assertEqual(len(baseline_fills), 1, "Causality test requires a real baseline signal")
        day["observations"] = [{"timestamp": decision - 3600, "fahrenheit": 70},
                               {"timestamp": decision - 60, "fahrenheit": 100}]
        # A high temperature exists in the archive but its 30-minute lag has not elapsed.
        fills, attempts, _ = run_strategy([day], cfg, fit, "observed_dead_bucket_no", 0)
        self.assertEqual(fills, [])
        self.assertEqual(attempts, [])

    def test_calibration_does_not_learn_from_out_of_sample_results(self):
        days = [{"date": (date(2026, 8, 1) + timedelta(days=n)).isoformat(),
                 "actual_iem_high": 80 + n % 3,
                 "model_maxes": {m: 80 for m in CONFIG["forecast_models"]}} for n in range(30)]
        before = calibrate(days, CONFIG)
        future = {"date": "2026-09-01", "actual_iem_high": 1000,
                  "model_maxes": {m: -1000 for m in CONFIG["forecast_models"]}}
        self.assertEqual(before, calibrate(days + [future], CONFIG))

    def test_early_close_blocks_both_signal_and_delayed_fill(self):
        cfg = dict(CONFIG, observed_decision_local_hours=[10])
        fit = {"weights": {cfg["forecast_models"][0]: 1}, "bias_f": 0, "sigma_f": 2}
        day, decision = sample_day(cfg)
        # On-chain resolution is tomorrow, but trading already stopped.
        day["markets"][0]["close_timestamp"] = decision - 1
        fills, attempts, _ = run_strategy([day], cfg, fit, "observed_dead_bucket_no", 0)
        self.assertEqual((fills, attempts), ([], []))
        # The same otherwise valid signal cannot execute after a near-term close.
        day["markets"][0]["close_timestamp"] = decision + 30
        fills, attempts, _ = run_strategy([day], cfg, fit, "observed_dead_bucket_no", 0)
        self.assertEqual(fills, [])
        self.assertEqual(attempts[0]["reason"], "market_closed_before_execution")

    def test_insufficient_study_capital_is_rejected_before_trading(self):
        cfg = dict(CONFIG, initial_capital=1, observed_decision_local_hours=[10])
        day, _ = sample_day(cfg)
        fit = {"weights": {cfg["forecast_models"][0]: 1}, "bias_f": 0, "sigma_f": 2}
        with self.assertRaisesRegex(ValueError, "capital"):
            run_strategy([day], cfg, fit, "observed_dead_bucket_no", 0)

    def test_overlapping_positions_reconcile_actual_settlement_cash(self):
        fills = [
            {"fill_timestamp": 10, "resolution_timestamp": 30, "cost": 4,
             "settlement": 0, "shares": 5, "pnl": -4, "fees": .1},
            {"fill_timestamp": 20, "resolution_timestamp": 40, "cost": 3,
             "settlement": 1, "shares": 5, "pnl": 2, "fees": .1},
        ]
        result = summarize(fills, [], [{"pnl": 2}, {"pnl": -4}], CONFIG)
        self.assertEqual(result["minimum_cash"], 993)
        self.assertEqual(result["maximum_reserved_cost"], 7)
        self.assertEqual(result["ending_cash"], 998)
        self.assertAlmostEqual(result["settlement_equity_max_drawdown_pct"], .4)


if __name__ == "__main__":
    unittest.main()
