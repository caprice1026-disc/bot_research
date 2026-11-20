"""Regression checks for calibration isolation and the later-day control."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_followup import persistence_rows, provider_fit


class FollowupTemporalTests(unittest.TestCase):
    def training(self):
        start = date(2026, 8, 1)
        return [{"date": (start + timedelta(days=i)).isoformat(), "actual_iem_high": 70 + i % 4,
                 "model_maxes": {"nbm": 69 + i % 4}} for i in range(30)]

    def cfg(self):
        return {"calibration_start": "2026-08-01", "calibration_end": "2026-08-30", "sigma_floor_fahrenheit": 2}

    def test_provider_fit_ignores_later_results_and_pre_window_data(self):
        rows = self.training()
        fit = provider_fit(rows, self.cfg(), "nbm")
        outside = [{"date": ds, "actual_iem_high": 999, "model_maxes": {"nbm": -999}}
                   for ds in ("2026-07-31", "2026-10-04")]
        self.assertEqual(fit, provider_fit(rows + outside, self.cfg(), "nbm"))
        self.assertEqual(fit["days"], 30)

    def test_calibration_missing_day_or_duplicate_is_rejected(self):
        rows = self.training()
        for altered in (rows[:-1], rows[:-1] + [rows[0]]):
            with self.assertRaises(ValueError):
                provider_fit(altered, self.cfg(), "nbm")

    def test_persistence_uses_only_prior_seven_calendar_days(self):
        highs = {(date(2026, 9, 27) + timedelta(days=i)).isoformat(): 70 + i for i in range(7)}
        highs.update({"2026-10-04": 999, "2026-10-05": -999})
        result = persistence_rows([{"date": "2026-10-04"}], highs)
        self.assertEqual(result[0]["model_maxes"]["persistence"], 73)

    def test_missing_control_history_is_not_a_shortened_or_zero_trade_day(self):
        with self.assertRaises(ValueError):
            persistence_rows([{"date": "2026-10-04"}], {"2026-10-03": 70})


if __name__ == "__main__":
    unittest.main()
