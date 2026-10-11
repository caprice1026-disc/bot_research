"""Independent non-vacuous checks of the trailing-TWAP probability model."""
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from btc_backtest import fair_probability, signal


CONFIG = json.loads((Path(__file__).resolve().parents[1] / "btc_config.json").read_text())


def row(ts, *, initial_oracle=99.9):
    value = {"decision_ts": ts, "slug": "review", "window_start_ts": 0,
        "window_end_ts": 300, "coinbase_price": 100., "coinbase_age_seconds": .1,
        "coinbase_source_ts": ts - .1,
        "chainlink60_price": initial_oracle if ts == 0 else 100.,
        "chainlink60_source_ts": ts - .1}
    for side in ("up", "down"):
        value.update({f"{side}_book_valid": True, f"{side}_bid": .49,
            f"{side}_ask": .50, f"{side}_ask_size": 100,
            f"{side}_quote_age_seconds": .1, f"{side}_quote_source_ts": ts - .1})
    return value


class IndependentTwapReview(unittest.TestCase):
    @patch("btc_backtest.statistics.stdev", return_value=.001)
    def test_integrated_brownian_variance_before_and_inside_final_window(self, _):
        for decision in (180, 270):
            history = [row(t) for t in range(decision + 1)]
            times = list(range(decision + 1))
            actual = fair_probability(history, times, history[-1], CONFIG)
            self.assertIsNotNone(actual)
            tte, window = 300 - decision, CONFIG["twap_window_seconds"]
            variance = tte - 2 * window / 3 if tte >= window else tte**3 / (3 * window**2)
            z = .1 / (100 * .001 * math.sqrt(variance))
            expected = .5 * (1 + math.erf(z / math.sqrt(2)))
            self.assertAlmostEqual(actual, expected, places=10)

    @patch("btc_backtest.statistics.stdev", return_value=.0001)
    def test_future_reference_and_oracle_spikes_cannot_change_a_real_signal(self, _):
        history = [row(t) for t in range(181)]
        times = list(range(181))
        before = signal("btc_fair_value", history, times, history[-1], CONFIG)
        self.assertIsNotNone(before, "Lookahead test must have an actual baseline signal")
        self.assertEqual(before[0], ["up"])
        future = dict(row(181), coinbase_price=1e9, chainlink60_price=1e9)
        after = signal("btc_fair_value", history + [future], times + [181], history[-1], CONFIG)
        self.assertEqual(before, after)

    def test_stale_oracle_and_missing_recent_second_are_rejected(self):
        history = [row(t) for t in range(181)]
        stale = [*history[:-1], dict(history[-1], chainlink60_source_ts=170)]
        self.assertIsNone(fair_probability(stale, list(range(181)), stale[-1], CONFIG))
        missing = [r for r in history if r["decision_ts"] != 160]
        self.assertIsNone(fair_probability(missing, [r["decision_ts"] for r in missing], missing[-1], CONFIG))


if __name__ == "__main__":
    unittest.main()
