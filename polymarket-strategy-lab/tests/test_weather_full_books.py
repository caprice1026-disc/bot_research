"""Adversarial checks for whole-cohort weather L2 selection and cash boundaries."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_full_book_backtest import book_reason, cost_for, run_scenario


class FullBookBoundaries(unittest.TestCase):
    def setUp(self):
        self.cfg = {"decision_utc_hour": 15, "execution_latency_seconds": 60,
            "forecast_min_net_edge": .08, "order_shares": 5.0, "initial_capital": 1000.0,
            "current_taker_fee_rate": .05, "max_price_move": .05,
            "bootstrap_seed": 1, "bootstrap_samples": 100}
        self.fit = {"weights": {"model": 1}, "bias_f": 0, "sigma_f": 2}

    def book(self, timestamp, ask=.1):
        return {"timestamp": timestamp, "book_book_valid": True, "book_bid": max(0, ask - .01),
            "book_ask": ask, "book_ask_size": 10, "book_quote_recv_ts": timestamp - 1,
            "book_quote_source_ts": timestamp - 2, "book_tick_size": None}

    def fixture(self, ds="2026-08-31"):
        timestamp = int(datetime.fromisoformat(ds).replace(hour=15, tzinfo=timezone.utc).timestamp())
        markets, snapshots = [], {}
        for cid, bounds in (("lower", (78, 79)), ("upper", (80, 81))):
            markets.append({"condition_id": cid, "title": str(bounds), "bounds": bounds,
                "close_timestamp": timestamp + 3600, "resolution_timestamp": timestamp + 7200,
                "yes_settlement": float(cid == "upper"), "resolution_transaction_hash": "tx-" + cid,
                "settlement_source": "NOAA_KORD_hourly"})
            for outcome in ("Yes", "No"):
                ask = .1 if outcome == "Yes" else .99
                snapshots[(ds, cid, outcome)] = {"token_id": cid + "-" + outcome,
                    "signal": self.book(timestamp, ask), "execution": self.book(timestamp + 60, ask)}
        return {"date": ds, "model_maxes": {"model": 80}, "markets": markets}, snapshots

    def test_freshness_strict_receive_and_source_separately(self):
        book = self.book(1000)
        self.assertIsNone(book_reason(book, 1000, 60, 5))
        book["book_bid"] = None  # Buying requires the actual ask; absent bids do not invent depth.
        self.assertIsNone(book_reason(book, 1000, 60, 5))
        book["book_quote_recv_ts"] = 1000
        self.assertEqual(book_reason(book, 1000, 60, 5), "quote_not_before_decision")
        book["book_quote_recv_ts"] = 999
        book["book_quote_source_ts"] = 939
        self.assertEqual(book_reason(book, 1000, 60, 5), "stale_or_future_source_quote")
        book["book_quote_source_ts"] = 1001
        self.assertEqual(book_reason(book, 1000, 60, 5), "stale_or_future_source_quote")

    def test_future_execution_does_not_change_candidate_selection(self):
        day, rows = self.fixture()
        rows[(day["date"], "upper", "Yes")]["execution"]["book_ask_size"] = 1
        result = run_scenario([day], rows, self.cfg, self.fit, 60, .01)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["condition_id"], "upper")
        self.assertEqual(result["attempts"][0]["reason"], "insufficient_top_depth")
        self.assertEqual(result["fills"], [])

    def test_no_uses_actual_distinct_token_ask(self):
        day, rows = self.fixture()
        selected = rows[(day["date"], "upper", "No")]
        selected["signal"] = self.book(selected["signal"]["timestamp"], .03)
        selected["execution"] = self.book(selected["execution"]["timestamp"], .05)
        result = run_scenario([day], rows, self.cfg, self.fit, 60, .01)
        fill = result["fills"][0]
        self.assertEqual(fill["outcome"], "No")
        self.assertEqual(fill["token_id"], "upper-No")
        self.assertEqual(fill["entry_price"], .06)
        self.assertEqual(fill["settlement"], 0)
        self.assertAlmostEqual(fill["cost"], .3141)
        self.assertAlmostEqual(fill["pnl"], -.3141)

    def test_early_close_and_execution_price_cap(self):
        day, rows = self.fixture()
        day["markets"][1]["close_timestamp"] = rows[(day["date"], "upper", "Yes")]["execution"]["timestamp"]
        result = run_scenario([day], rows, self.cfg, self.fit, 60, .01)
        self.assertEqual(result["attempts"][0]["reason"], "market_closed_before_execution")
        self.assertFalse(result["fills"])
        day, rows = self.fixture()
        rows[(day["date"], "upper", "Yes")]["execution"] = self.book(rows[(day["date"], "upper", "Yes")]["execution"]["timestamp"], .15)
        result = run_scenario([day], rows, self.cfg, self.fit, 60, .01)
        self.assertEqual(result["attempts"][0]["reason"], "execution_price_limit")

    def test_runtime_cash_locked_until_official_resolution(self):
        day1, rows1 = self.fixture()
        day2, rows2 = self.fixture("2026-09-01")
        day1["markets"][1]["resolution_timestamp"] += 2 * 86400
        cfg = {**self.cfg, "initial_capital": .8}
        result = run_scenario([day1, day2], {**rows1, **rows2}, cfg, self.fit, 60, .01)
        self.assertEqual(len(result["fills"]), 1)
        self.assertEqual(result["attempts"][1]["reason"], "insufficient_cash")
        self.assertAlmostEqual(result["summary"]["ending_cash"], .8 + result["fills"][0]["pnl"])
        self.assertGreaterEqual(result["summary"]["minimum_cash"], 0)

    def test_fee_precision_and_stale_all_cohort_is_zero_evidence(self):
        cost, fee = cost_for(.333, self.cfg)
        self.assertEqual(fee, .05553)
        self.assertAlmostEqual(cost, 1.72053)
        day, rows = self.fixture()
        for row in rows.values():
            row["signal"]["book_quote_source_ts"] -= 301
        result = run_scenario([day], rows, self.cfg, self.fit, 60, .01)
        self.assertEqual(result["summary"]["fills"], 0)
        self.assertEqual(result["summary"]["three_day_block_bootstrap_pnl_95pct"], [None, None])
        self.assertIsNone(result["summary"]["cost_roi_pct"])


if __name__ == "__main__":
    unittest.main()
