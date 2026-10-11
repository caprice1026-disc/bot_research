import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_deep_research import decision_time, sample_times, validate_rules
from weather_book_check import sample_books
from weather_deep_benchmark import persistence_days


class DeepWeatherBoundaries(unittest.TestCase):
    def event(self, at, ask=.4):
        return {"source": "polymarket", "symbol": "1", "event_type": "book", "recv_ts": at,
                "source_event_ts": int(at * 1e6), "connection_id": "archive", "bid": .3, "ask": ask,
                "raw_payload": json.dumps({"bids": [{"price": ".3", "size": "20"}], "asks": [{"price": str(ask), "size": "30"}]})}

    def test_multi_delay_replay_matches_original_and_excludes_exact_boundary(self):
        events = [self.event(99), self.event(100, .9), self.event(159, .5), self.event(160, .8), self.event(399, .6)]
        result, _ = sample_times(events, "1", 100, [1, 30, 60, 120, 300])
        prior, _ = sample_books(events, "1", 100, 60, allow_one_sided=True)
        for key in ("book_ask", "book_ask_size", "book_quote_recv_ts", "book_book_valid"):
            self.assertEqual(result["0"][key], prior["signal"][key])
            self.assertEqual(result["60"][key], prior["execution"][key])
        self.assertEqual(result["0"]["book_ask"], .4)
        self.assertEqual(result["60"]["book_ask"], .5)
        self.assertEqual(result["300"]["book_ask"], .6)

    def test_future_seed_does_not_initialize_signal(self):
        result, _ = sample_times([self.event(101)], "1", 100, [60])
        self.assertFalse(result["0"]["initial_snapshot_present"])
        self.assertIsNone(result["0"]["book_ask"])
        self.assertTrue(result["60"]["initial_snapshot_present"])

    def test_city_local_ten_is_not_a_shared_utc_hour(self):
        cfg = {"decision_local_hour": 10}
        for offset, expected in ((-5, 15), (-4, 14)):
            ts = decision_time("2026-09-01", cfg, {"utc_offset_hours": offset})
            self.assertEqual(datetime.fromtimestamp(ts, timezone.utc).hour, expected)

    def test_station_guard_and_transition_convention_are_preserved(self):
        p = {"station": "KLGA", "station_phrase": "LaGuardia Airport Station"}
        market = {"description": "LaGuardia Airport Station whole degrees Fahrenheit", "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=klga"}
        self.assertIn("frequency_unspecified", validate_rules(market, p))
        altered = copy.deepcopy(market)
        altered["resolutionSource"] = "https://www.weather.gov/wrh/timeseries?site=katl"
        with self.assertRaises(ValueError):
            validate_rules(altered, p)

    def test_persistence_features_do_not_use_same_day_or_future_temperature(self):
        days = [{"date": f"2026-08-{i:02d}", "actual_iem_high": 70 + i} for i in range(1, 20)]
        first = persistence_days(days)
        changed = copy.deepcopy(days)
        for d in changed:
            if d["date"] >= "2026-08-12":
                d["actual_iem_high"] = 120
        second = persistence_days(changed)
        self.assertEqual(next(d for d in first if d["date"] == "2026-08-12")["model_maxes"],
                         next(d for d in second if d["date"] == "2026-08-12")["model_maxes"])


if __name__ == "__main__":
    unittest.main()
