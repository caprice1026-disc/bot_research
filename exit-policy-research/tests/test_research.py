import importlib.util
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

SPEC = importlib.util.spec_from_file_location("research", Path(__file__).parents[1] / "research.py")
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


def config():
    import json
    return json.loads((Path(__file__).parents[1] / "config.json").read_text())


def bars(n=3000):
    t = np.arange(n, dtype="int64") * r.BAR_MS + 1717200000000
    return pd.DataFrame({"open_ms": t, "available_ms": t + r.BAR_MS,
                         "open": np.full(n, 100.), "high": np.full(n, 100.1),
                         "low": np.full(n, 99.9), "close": np.full(n, 100.), "volume": np.full(n, 1.)})


class ExecutionTests(unittest.TestCase):
    def test_both_barriers_stop_first(self):
        self.assertEqual(r.protective_exit(100, 110, 90, 96, 108), (96., "stop"))

    def test_gap_stop_at_worse_open(self):
        self.assertEqual(r.protective_exit(90, 100, 89, 96, 108), (90., "gap_stop"))

    def test_gap_target_capped(self):
        self.assertEqual(r.protective_exit(115, 120, 114, 96, 108), (108., "gap_tp_capped"))

    def test_next_open_uses_no_same_close(self):
        cfg = config()
        d = bars()
        d.loc[2051, "close"] = 101
        d.loc[2051, "high"] = 101.1
        d.loc[2052, ["open", "high", "low", "close"]] = [102, 102.1, 101.9, 102]
        e = r.make_episode(d, r.market_features(d), 2028, cfg, "BTC")
        j = list(e["offsets"]).index(24)
        self.assertEqual(e["candidates"][j][:2], (24, 102.))
        self.assertEqual(e["x"][j, 10], np.float32(101 / 100.05 - 1))

    def test_extra_delay_and_intervening_stop(self):
        cfg = config()
        d = bars()
        d.loc[2052, "low"] = 90
        e = r.make_episode(d, r.market_features(d), 2028, cfg, "BTC", extra_delay=1)
        j = list(e["offsets"]).index(24)
        self.assertEqual(e["candidates"][j][0], 24)
        self.assertEqual(e["candidates"][j][2], "stop")

    def test_market_open_precedes_same_bar_intrabar_stop(self):
        cfg = config()
        d = bars()
        d.loc[2052, "low"] = 90
        e = r.make_episode(d, r.market_features(d), 2028, cfg, "BTC")
        j = list(e["offsets"]).index(24)
        self.assertEqual(e["candidates"][j][2], "policy")

    def test_costs_and_no_borrowing(self):
        x = r.cash_result(1000, 100, 110, config())
        self.assertAlmostEqual(x["qty"] * x["entry_fill"] * 1.001, 1000)
        self.assertAlmostEqual(x["gross_pnl"] - x["cost"], x["net_pnl"])
        self.assertGreater(x["fees"], 0)
        self.assertGreater(x["slippage"], 0)

    def test_round_trip_flat_loses_cost(self):
        self.assertLess(r.cash_result(1000, 100, 100, config())["net_pnl"], 0)

    def test_minimum_hold_mask(self):
        d = bars()
        e = r.make_episode(d, r.market_features(d), 2028, config(), "BTC")
        self.assertFalse(e["valid"][0])
        self.assertTrue(e["valid"][1])

    def test_forced_stop_before_minimum_hold(self):
        d = bars()
        d.loc[2029, "low"] = 90
        e = r.make_episode(d, r.market_features(d), 2028, config(), "BTC")
        self.assertEqual(len(e["x"]), 0)
        self.assertEqual(e["forced"][0], 1)


class TerminalBoundaryTests(unittest.TestCase):
    def episode(self, terminal_open=100., terminal_high=None, terminal_low=None,
                cost_mult=1, previous_low=None):
        cfg = config()
        d = bars()
        start = 2028
        terminal = start + cfg["max_hold_hours"] * 12
        d.loc[terminal, ["open", "high", "low", "close"]] = [
            terminal_open, terminal_high if terminal_high is not None else terminal_open + .1,
            terminal_low if terminal_low is not None else terminal_open - .1, terminal_open]
        if previous_low is not None:
            d.loc[terminal - 1, "low"] = previous_low
        ep = r.make_episode(d, r.market_features(d), start, cfg, "BTC", cost_mult=cost_mult)
        return ep, cfg

    def test_terminal_gap_tp_capped_before_max_hold(self):
        ep, cfg = self.episode(120.)
        target = 100 * (1 + cfg["slippage_bps"] * 1e-4) * (1 + cfg["take_profit"])
        self.assertEqual(ep["forced"], (576, target, "gap_tp_capped"))
        x = r.cash_result(50000., 100., ep["forced"][1], cfg)
        self.assertAlmostEqual(x["end_cash"], 53865.16183816186, places=6)

    def test_terminal_gap_stop_precedes_max_hold(self):
        ep, _ = self.episode(90.)
        self.assertEqual(ep["forced"], (576, 90., "gap_stop"))

    def test_terminal_exact_tp_precedes_max_hold(self):
        cfg = config()
        target = 100 * (1 + cfg["slippage_bps"] * 1e-4) * (1 + cfg["take_profit"])
        ep, _ = self.episode(target)
        self.assertEqual(ep["forced"], (576, target, "gap_tp_capped"))

    def test_terminal_exact_stop_precedes_max_hold(self):
        cfg = config()
        stop = 100 * (1 + cfg["slippage_bps"] * 1e-4) * (1 - cfg["stop_loss"])
        ep, _ = self.episode(stop)
        self.assertEqual(ep["forced"], (576, stop, "gap_stop"))

    def test_terminal_open_inside_barriers_is_max_hold(self):
        ep, _ = self.episode(100.)
        self.assertEqual(ep["forced"], (576, 100., "max_hold"))

    def test_terminal_high_low_are_not_used(self):
        # Both barriers get touched only AFTER the terminal open liquidation.
        ep, _ = self.episode(100., terminal_high=150., terminal_low=50.)
        self.assertEqual(ep["forced"], (576, 100., "max_hold"))

    def test_previous_bar_stop_precedes_terminal_gap_tp(self):
        ep, _ = self.episode(120., previous_low=90.)
        self.assertEqual(ep["forced"][0], 575)
        self.assertEqual(ep["forced"][2], "stop")

    def test_terminal_gap_uses_stressed_fill_anchor(self):
        ep, cfg = self.episode(120., cost_mult=2)
        target = 100 * (1 + cfg["slippage_bps"] * 2e-4) * (1 + cfg["take_profit"])
        self.assertEqual(ep["forced"], (576, target, "gap_tp_capped"))


class LeakageAndLearningTests(unittest.TestCase):
    def test_future_price_changes_do_not_change_past_features(self):
        d = bars()
        x = r.market_features(d)
        d.loc[2500:, ["open", "close", "high", "low", "volume"]] *= 2
        np.testing.assert_array_equal(x[:2500], r.market_features(d)[:2500])

    def test_oracle_train_only(self):
        with self.assertRaises(ValueError):
            r.oracle_labels({}, config(), split="test")

    def test_oracle_earliest_equal_maximum(self):
        d = bars()
        e = r.make_episode(d, r.market_features(d), 2028, config(), "BTC")
        y, j = r.oracle_labels(e, config())
        self.assertEqual(j, 1)
        self.assertEqual(list(y[:3]), [0, 1, 1])

    def test_scaler_only_uses_passed_training_rows(self):
        s = r.fit_scaler([{"x": np.zeros((3, len(r.FEATURES)), dtype="float32")}])
        np.testing.assert_array_equal(s["mean"], np.zeros(len(r.FEATURES)))
        self.assertTrue(np.isfinite(r.transform(np.ones((1, len(r.FEATURES))), s)).all())

    def test_return_alignment_includes_forced_elapsed_time(self):
        out = r.return_to_go(2, 36, [12, 24], .9)
        np.testing.assert_allclose(out, [2 * .9**2, 2 * .9])

    def test_episode_full_path_purge_and_shared_entries(self):
        cfg = config()
        cfg["warmup_days"] = 7
        cfg["entry_stride_hours"] = 3
        cfg["max_hold_hours"] = 2
        cfg["min_hold_hours"] = 1
        d = bars(12000)
        sp, edges, purged, _ = r.build_splits({"BTC": d, "ETH": d.copy()}, cfg)
        self.assertTrue(purged)
        for i, name in enumerate(("train", "val", "test")):
            for e in sp[name]:
                self.assertGreaterEqual(e["entry_ms"], edges[i])
                self.assertLess(e["end_ms"], edges[i + 1])
            self.assertEqual([e["entry_ms"] for e in sp[name] if e["asset"] == "BTC"],
                             [e["entry_ms"] for e in sp[name] if e["asset"] == "ETH"])

    def test_cash_waits_until_common_next_entry(self):
        cfg = config()
        d = bars(5000)
        mf = r.market_features(d)
        eps = [r.make_episode(d, mf, i, cfg, "BTC") for i in (2028, 2616)]
        metrics, trades = r.evaluate({"BTC": d}, eps, [int(d.open_ms.iloc[2016]), int(d.open_ms.iloc[4000])], cfg, "fixed_12h", None, None)
        self.assertEqual([t["entry_ms"] for t in trades], [e["entry_ms"] for e in eps])
        self.assertEqual(len(trades), 2)
        self.assertLess(metrics["exposure_mean"], .2)
        self.assertAlmostEqual(metrics["net_profit"], sum(t["net_pnl"] for t in trades))


if __name__ == "__main__":
    unittest.main()
