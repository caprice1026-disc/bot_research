from dataclasses import replace
from decimal import Decimal as D
import unittest

import test_research as h
from swingguard.data import BAR_MS, DAY_MS, date_ms
from swingguard.engine import Portfolio, run_engine


START = date_ms("2024-01-02")


def run(provider=None, data=None, c=None, arm="uncontrolled", days=2):
    return run_engine(data or h.flat_data(days + 1), c or h.config(), START, START + days * DAY_MS,
                      arm, provider or h.FixedProvider(), features_fn=lambda data, index: {})


def set_price(data, first_index, price):
    p = D(price)
    for index in range(first_index, len(data["BTCUSDT"])):
        data["BTCUSDT"][index] = replace(data["BTCUSDT"][index], open=p, high=p, low=p, close=p)


class DayBoundaryTests(unittest.TestCase):
    def test_last_closed_bar_uses_its_original_day_basis(self):
        for index in (574, 575, 576):  # 23:50, 23:55 and 00:00 bars
            with self.subTest(bar=index):
                c, data = h.config(), h.flat_data(3)
                c.update(commission_bps="0", slippage_bps="0")
                set_price(data, index, "93")
                result = run(data=data, c=c, arm="post_only")
                expected = data["BTCUSDT"][index].available_ms
                transition = next(x for x in result["transitions"] if x["time_ms"] == expected)
                self.assertEqual(transition["state"], "SURVIVAL")
                self.assertAlmostEqual(transition["context"]["daily_loss"], .07)
                if index == 575:
                    self.assertEqual(transition["context"]["daily_loss_basis_start_ms"], START)
                    self.assertAlmostEqual(transition["context"]["completed_day_loss"], .07)
                    self.assertEqual(transition["context"]["current_day_loss"], 0)

    def test_midnight_does_not_erase_loss_to_allow_early_recovery(self):
        c, data = h.config(), h.flat_data(3)
        c.update(commission_bps="0", slippage_bps="0")
        c["risk"]["survival_min_ms"] = 0
        # Isolate the daily-loss reset; the separate one-hour shock also blocks recovery.
        c["risk"]["alert_market_drop_1h"] = .20
        c["risk"]["survival_market_drop_1h"] = .30
        set_price(data, 574, "93")
        result = run(data=data, c=c, arm="post_only")
        recovery = next(x for x in result["transitions"] if x["state"] == "RECOVERY")
        self.assertEqual(recovery["time_ms"], START + DAY_MS + BAR_MS)

    def test_pre_provider_receives_completed_and_new_day_losses_separately(self):
        class Capturing(h.FixedProvider):
            def __init__(self):
                super().__init__()
                self.contexts = {}

            def propose(self, timestamp, features, risk=None):
                self.contexts[timestamp] = risk
                return super().propose(timestamp, features, risk)
        c, data, p = h.config(), h.flat_data(3), Capturing()
        c.update(commission_bps="0", slippage_bps="0")
        set_price(data, 575, "93")
        run(p, data, c, arm="pre_risk")
        risk = p.contexts[START + DAY_MS]
        self.assertAlmostEqual(risk["daily_loss"], .07)
        self.assertAlmostEqual(risk["completed_day_loss"], .07)
        self.assertEqual(risk["current_day_loss"], 0)


class RenewalTests(unittest.TestCase):
    def test_identical_daily_proposals_do_not_force_round_trips_with_one_ms_delay(self):
        zero, delayed = run(h.FixedProvider(), days=3), run(h.FixedProvider(latency=1), days=3)
        self.assertEqual(zero["metrics"]["fill_count"], 2)
        self.assertEqual(delayed["metrics"]["fill_count"], 2)
        self.assertEqual(zero["metrics"]["profit"], delayed["metrics"]["profit"])
        self.assertFalse(any(t["reason"] == "expired_signal" for t in delayed["trades"]))
        self.assertTrue(any(row["proposal_grace"] for row in delayed["equity"]))

    def test_valid_update_switches_at_first_open_after_receipt(self):
        class CloseNextDay(h.FixedProvider):
            def propose(self, timestamp, features, risk=None):
                p = super().propose(timestamp, features, risk)
                return replace(p, weights={"BTCUSDT": "0", "ETHUSDT": "0"}) if timestamp > START else p
        result = run(CloseNextDay(latency=1))
        sell = next(t for t in result["trades"] if t["side"] == "sell")
        self.assertEqual(sell["event_time_ms"], START + DAY_MS + BAR_MS)
        self.assertEqual(sell["reason"], "daily_proposal")

    def test_no_response_flattens_at_fixed_grace_deadline_and_stays_flat(self):
        class Missing(h.FixedProvider):
            def propose(self, timestamp, features, risk=None):
                return super().propose(timestamp, features, risk) if timestamp == START else None
        result = run(Missing(), days=3)
        self.assertEqual(result["metrics"]["fill_count"], 2)
        sell = result["trades"][1]
        self.assertEqual(sell["event_time_ms"], START + DAY_MS + 900_000)
        self.assertEqual(sell["reason"], "expired_signal")

    def test_late_update_is_discarded_without_reentry(self):
        class Late(h.FixedProvider):
            def propose(self, timestamp, features, risk=None):
                p = super().propose(timestamp, features, risk)
                return replace(p, received_ms=timestamp + 900_001) if timestamp > START else p
        result = run(Late())
        self.assertEqual(result["metrics"]["fill_count"], 2)
        self.assertEqual(result["trades"][1]["event_time_ms"], START + DAY_MS + 900_000)
        self.assertEqual(result["trades"][1]["reason"], "failed_response")

    def test_exact_latency_deadline_is_valid(self):
        result = run(h.FixedProvider(latency=900_000))
        self.assertEqual(result["metrics"]["fill_count"], 2)
        self.assertEqual(result["trades"][0]["event_time_ms"], START + 900_000)

    def test_invalid_response_closes_on_receipt_instead_of_waiting_full_grace(self):
        class Invalid(h.FixedProvider):
            def propose(self, timestamp, features, risk=None):
                p = super().propose(timestamp, features, risk)
                return replace(p, status="invalid_response_flat_fallback", weights={"BTCUSDT": "0", "ETHUSDT": "0"}) if timestamp > START else p
        result = run(Invalid(latency=1))
        self.assertEqual(result["trades"][1]["event_time_ms"], START + DAY_MS + BAR_MS)
        self.assertEqual(result["trades"][1]["reason"], "failed_response")

    def test_grace_permits_risk_reduction_but_not_buying(self):
        c = h.config()
        c.update(commission_bps="0", slippage_bps="0")
        account = Portfolio(c["symbols"], c)
        account.fill("BTCUSDT", D(1), D(100), 0, "initial", 0)
        bars = {s: h.flat_data(1)[s][0] for s in c["symbols"]}
        prices = {s: D(100) for s in c["symbols"]}
        account.rebalance({"BTCUSDT": D(1), "ETHUSDT": D(0)}, D(10000), prices, bars, "grace", reductions_only=True)
        self.assertEqual(account.quantities["BTCUSDT"], 1)
        account.rebalance({"BTCUSDT": D(0), "ETHUSDT": D(0)}, D(10000), prices, bars, "risk", reductions_only=True)
        self.assertEqual(account.quantities["BTCUSDT"], 0)

    def test_recovery_cap_increase_cannot_buy_during_renewal_grace(self):
        c, data = h.config(), h.flat_data(3)
        c.update(commission_bps="0", slippage_bps="0")
        set_price(data, 432, "95")
        set_price(data, 575, "104")
        result = run(h.FixedProvider(latency=600_000), data, c, arm="post_only")
        later_buys = [t for t in result["trades"] if t["side"] == "buy" and t["event_time_ms"] >= START + DAY_MS]
        self.assertTrue(later_buys)
        self.assertEqual(later_buys[0]["event_time_ms"], START + DAY_MS + 600_000)

    def test_stop_while_response_pending_is_not_erased_by_its_arrival(self):
        data = h.flat_data(3)
        data["BTCUSDT"][576] = replace(data["BTCUSDT"][576], low=D(80))
        result = run(h.FixedProvider(latency=1), data)
        self.assertEqual(sum(t["side"] == "buy" for t in result["trades"]), 1)
        self.assertEqual(result["trades"][1]["reason"], "stop")

    def test_unbounded_grace_configuration_rejected(self):
        c = h.config()
        c["proposal_renewal_grace_ms"] = 900_001
        with self.assertRaises(ValueError):
            run(c=c)


if __name__ == "__main__":
    unittest.main()
