from dataclasses import replace
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from swingguard.data import BAR_MS, DAY_MS, Bar, date_ms, parse_archive_row, validate_bars
from swingguard.engine import Portfolio, market_features, run_engine
from swingguard.providers import GeminiProvider, Proposal, RuleProvider, budget_lock, proposal_valid, validate_weights
from swingguard.risk import RiskController

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs/default.json"


def config():
    return json.loads(CONFIG_PATH.read_text())


def flat_data(days=2, price="100"):
    start = date_ms("2024-01-01")
    p = D(price)
    return {s: [Bar(start + i * BAR_MS, p, p, p, p, D(1)) for i in range(days * 288)]
            for s in config()["symbols"]}


class FixedProvider:
    def __init__(self, latency=0, created_offset=0):
        self.latency, self.created_offset = latency, created_offset

    def propose(self, timestamp, features, risk=None):
        return Proposal(timestamp + self.created_offset, timestamp + self.latency,
                        timestamp + DAY_MS, {"BTCUSDT": "1", "ETHUSDT": "0"}, "ok", "test", "fixed", "test")


def run_fixture(provider=None, arm="uncontrolled", data=None, c=None):
    data = data or flat_data()
    start = date_ms("2024-01-02")
    return run_engine(data, c or config(), start, start + DAY_MS, arm, provider or FixedProvider(),
                      features_fn=lambda rows, index: {})


class DataTests(unittest.TestCase):
    def row(self, scale):
        t = date_ms("2024-08-01")
        return [str(t * scale), "100", "110", "90", "105", "1", str((t + BAR_MS) * scale - 1)]

    def test_milliseconds_microseconds_identical(self):
        self.assertEqual(parse_archive_row(self.row(1)), parse_archive_row(self.row(1000)))

    def test_seconds_rejected(self):
        row = self.row(1)
        row[0] = str(int(row[0]) // 1000)
        with self.assertRaises(ValueError):
            parse_archive_row(row)

    def test_wrong_close_time_rejected(self):
        row = self.row(1000)
        row[6] = str(int(row[6]) - 1)
        with self.assertRaises(ValueError):
            parse_archive_row(row)

    def test_nonfinite_and_inconsistent_ohlc_rejected(self):
        for high in ("NaN", "99"):
            row = self.row(1)
            row[2] = high
            with self.assertRaises(ValueError):
                parse_archive_row(row)

    def test_duplicate_and_gap_rejected(self):
        rows = flat_data()["BTCUSDT"]
        for bad in (rows[:-1], [rows[0], rows[0], *rows[2:]], [rows[1], rows[0], *rows[2:]]):
            with self.assertRaises(ValueError):
                validate_bars(bad, rows[0].open_ms, rows[-1].available_ms)

    def test_completed_bar_features_do_not_see_current_or_future(self):
        rows = flat_data(61)
        index = 60 * 288
        before = market_features(rows, index)
        for symbol in rows:
            rows[symbol][index] = replace(rows[symbol][index], high=D(900), close=D(900))
        self.assertEqual(market_features(rows, index), before)
        self.assertEqual(before["BTCUSDT"]["latest_bar_available_ms"], rows["BTCUSDT"][index].open_ms)


class ProposalTests(unittest.TestCase):
    def test_joint_weights_normalize_without_leverage(self):
        self.assertEqual(validate_weights({"BTCUSDT": "1", "ETHUSDT": "1"}, config()["symbols"]),
                         {"BTCUSDT": D("0.5"), "ETHUSDT": D("0.5")})

    def test_invalid_short_nan_numeric_weight_rejected(self):
        for bad in ("-1", "NaN", "Infinity", 0.5):
            with self.assertRaises((ValueError, ArithmeticError)):
                validate_weights({"BTCUSDT": bad, "ETHUSDT": "0"}, config()["symbols"])

    def test_stale_delayed_expired_and_future_signal(self):
        p = FixedProvider().propose(DAY_MS, {})
        self.assertTrue(proposal_valid(p, DAY_MS, DAY_MS, DAY_MS))
        self.assertFalse(proposal_valid(p, 0, DAY_MS, DAY_MS))
        self.assertFalse(proposal_valid(p, DAY_MS, 2 * DAY_MS, DAY_MS))
        self.assertFalse(proposal_valid(replace(p, received_ms=DAY_MS + 1), DAY_MS, DAY_MS, DAY_MS))

    def test_cache_and_risk_input_change_request_identity(self):
        with tempfile.TemporaryDirectory() as root:
            provider = RuleProvider(config(), Path(root))
            f = {s: {"close": 105, "sma20": 100, "sma60": 110, "return7d": .01} for s in config()["symbols"]}
            ordinary = provider.propose(DAY_MS, f)
            same = provider.propose(DAY_MS, f)
            cautious = provider.propose(DAY_MS, f, {"state": "ALERT"})
            self.assertEqual(ordinary, same)
            self.assertTrue(provider.audit[1]["cache_hit"])
            self.assertNotEqual(ordinary.input_hash, cautious.input_hash)
            self.assertNotEqual(ordinary.weights, cautious.weights)

    def test_invalid_response_flat_fallback_is_cached(self):
        class BadProvider(RuleProvider):
            def generate(self, request):
                return {"weights": {"BTCUSDT": "-1", "ETHUSDT": "0"}, "rationale": "bad"}, 0, "0", "fake"
        with tempfile.TemporaryDirectory() as root:
            provider = BadProvider(config(), Path(root))
            p = provider.propose(DAY_MS, {})
            self.assertEqual(p.status, "invalid_response_flat_fallback")
            self.assertTrue(all(D(w) == 0 for w in p.weights.values()))
            self.assertEqual(provider.propose(DAY_MS, {}), p)


class RiskTests(unittest.TestCase):
    def test_alert_survival_liquidation_cap_cooldown_and_staged_return(self):
        c = config()["risk"]
        c.update(survival_min_ms=10, recovery_step_ms=10)
        risk = RiskController(c, 100)
        self.assertEqual(risk.update(0, 95, 100, 0)["state"], "ALERT")
        self.assertEqual(risk.cap, D(".5"))
        self.assertEqual(risk.update(1, 87, 100, 0)["state"], "SURVIVAL")
        self.assertEqual(risk.cap, 0)
        self.assertEqual(risk.update(5, 87, 87, 0)["state"], "SURVIVAL")
        self.assertEqual(risk.update(11, 87, 87, 0)["state"], "RECOVERY")
        self.assertEqual(risk.cap, D(".25"))
        for timestamp, cap in ((21, ".5"), (31, ".75"), (41, "1")):
            risk.update(timestamp, 87, 87, 0)
            self.assertEqual(risk.cap, D(cap))
        self.assertEqual(risk.update(51, 87, 87, 0)["state"], "NORMAL")
        self.assertEqual(risk.episode_peak, 87)

    def test_recovery_redrop_reenters_survival(self):
        c = config()["risk"]
        c["survival_min_ms"] = 1
        risk = RiskController(c, 100)
        risk.update(0, 87, 100, 0)
        risk.update(1, 87, 87, 0)
        self.assertEqual(risk.update(2, 83, 87, 0)["state"], "SURVIVAL")

    def test_market_shock_without_portfolio_loss_stops_entry(self):
        risk = RiskController(config()["risk"], 100)
        self.assertEqual(risk.update(0, 100, 100, .09)["state"], "SURVIVAL")


class ExecutionTests(unittest.TestCase):
    def account(self):
        c = config()
        c.update(commission_bps="0", slippage_bps="0")
        account = Portfolio(c["symbols"], c)
        account.fill("BTCUSDT", D(1), D(100), 0, "test", 0)
        return account

    def test_gap_stop_uses_adverse_open(self):
        account = self.account()
        bar = Bar(0, D(80), D(90), D(70), D(85), D(1))
        locked = set()
        account.protect({"BTCUSDT": bar}, locked, gaps_only=True)
        self.assertEqual(account.trades[-1]["reference_price"], "80")
        self.assertEqual(account.trades[-1]["reason"], "stop_gap")
        self.assertIn("BTCUSDT", locked)

    def test_both_barriers_use_stop_first(self):
        account = self.account()
        bar = Bar(0, D(100), D(120), D(90), D(100), D(1))
        account.protect({"BTCUSDT": bar}, set())
        self.assertEqual(D(account.trades[-1]["reference_price"]), D(92))
        self.assertEqual(account.ambiguous_bars, 1)
        self.assertEqual(account.trades[-1]["timing"], "intrabar_unknown")

    def test_positive_target_gap_is_conservative(self):
        account = self.account()
        bar = Bar(0, D(130), D(140), D(125), D(135), D(1))
        account.protect({"BTCUSDT": bar}, set(), gaps_only=True)
        self.assertEqual(D(account.trades[-1]["reference_price"]), D(116))

    def test_scale_in_retains_initial_tp_and_never_loosens_stop(self):
        account = self.account()
        account.fill("BTCUSDT", D(1), D(110), BAR_MS, "add", BAR_MS)
        self.assertEqual(account.average["BTCUSDT"], D(105))
        self.assertEqual(account.stops["BTCUSDT"], D("96.6"))
        self.assertEqual(account.targets["BTCUSDT"], D(116))
        account.fill("BTCUSDT", D(1), D(100), 2 * BAR_MS, "add_lower", 2 * BAR_MS)
        self.assertEqual(account.stops["BTCUSDT"], D("96.6"))
        self.assertEqual(account.targets["BTCUSDT"], D(116))

    def test_initial_entry_at_next_open_after_known_close(self):
        data = flat_data()
        # Last known close 50, next open 100: buying at 50 would fabricate profit.
        previous = data["BTCUSDT"][287]
        data["BTCUSDT"][287] = replace(previous, open=D(50), high=D(50), low=D(50), close=D(50))
        result = run_fixture(data=data)
        trade = result["trades"][0]
        self.assertEqual(trade["event_time_ms"], date_ms("2024-01-02"))
        self.assertEqual(D(trade["reference_price"]), D(100))
        self.assertLess(result["metrics"]["profit"], 0)

    def test_model_latency_waits_for_following_open(self):
        result = run_fixture(FixedProvider(latency=1))
        self.assertEqual(result["trades"][0]["event_time_ms"], date_ms("2024-01-02") + BAR_MS)

    def test_stale_provider_cannot_open(self):
        result = run_fixture(FixedProvider(created_offset=-DAY_MS))
        self.assertEqual(result["metrics"]["fill_count"], 0)
        self.assertTrue(any(d["status"] == "stale_or_expired_flat_fallback" for d in result["decisions"]))

    def test_signal_expiry_closes_at_next_open(self):
        c = config()
        c["proposal_ttl_ms"] = BAR_MS
        class ShortTTL(FixedProvider):
            def propose(self, timestamp, features, risk=None):
                return replace(super().propose(timestamp, features, risk), expires_ms=timestamp + BAR_MS)
        result = run_fixture(ShortTTL(), c=c)
        self.assertEqual(result["trades"][1]["reason"], "expired_signal")
        self.assertEqual(result["trades"][1]["event_time_ms"], date_ms("2024-01-02") + BAR_MS)

    def test_costs_terminal_flat_and_cash_no_borrowing(self):
        result = run_fixture(arm="buy_hold")
        self.assertEqual(result["metrics"]["fill_count"], 4)
        self.assertEqual(result["metrics"]["closed_position_lifecycles"], 2)
        self.assertGreater(result["metrics"]["commission"], 0)
        self.assertGreater(result["metrics"]["slippage_cost"], 0)
        self.assertAlmostEqual(result["metrics"]["profit"], -result["metrics"]["commission"] - result["metrics"]["slippage_cost"], places=8)
        self.assertTrue(all(D(row["cash"]) >= D("-1e-16") and D(row["exposure"]) <= 1 for row in result["equity"]))
        self.assertEqual(result["equity"][-1]["exposure"], "0")

    def test_runtime_gap_rejected(self):
        data = flat_data()
        data["ETHUSDT"][300] = data["ETHUSDT"][299]
        with self.assertRaises(ValueError):
            run_fixture(data=data)

    def test_stop_does_not_reopen_during_same_daily_plan(self):
        data = flat_data()
        data["BTCUSDT"][288] = replace(data["BTCUSDT"][288], low=D(80))
        result = run_fixture(data=data)
        self.assertEqual(sum(t["side"] == "buy" for t in result["trades"]), 1)

    def test_survival_prevents_new_exposure(self):
        data = flat_data()
        for s in data:
            data[s][287] = replace(data[s][287], open=D(80), high=D(80), low=D(80), close=D(80))
        result = run_fixture(arm="post_only", data=data)
        self.assertGreaterEqual(result["trades"][0]["event_time_ms"], date_ms("2024-01-02") + 43_200_000)
        self.assertGreater(result["state_bars"]["SURVIVAL"], 0)

    def test_survival_liquidates_existing_position_at_next_open(self):
        data = flat_data()
        data["BTCUSDT"][299] = replace(data["BTCUSDT"][299], low=D(93), close=D(93))
        for i in range(300, len(data["BTCUSDT"])):
            data["BTCUSDT"][i] = replace(data["BTCUSDT"][i], open=D(93), high=D(93), low=D(93), close=D(93))
        result = run_fixture(arm="post_only", data=data)
        close = next(t for t in result["trades"] if t["side"] == "sell")
        self.assertEqual(close["event_time_ms"], date_ms("2024-01-02") + 12 * BAR_MS)
        self.assertEqual(close["reason"], "risk_control")


class GeminiJournalTests(unittest.TestCase):
    def fake_types(self):
        fake = types.ModuleType("google.genai")
        fake.types = types.SimpleNamespace(**{name: (lambda **kw: kw) for name in (
            "GenerateContentConfig", "ThinkingConfig", "AutomaticFunctionCallingConfig")})
        google = types.ModuleType("google")
        google.genai = fake
        return {"google": google, "google.genai": fake}

    def test_invalid_json_is_safe_flat_and_cached_without_second_call(self):
        response = types.SimpleNamespace(text="not JSON", model_version="gemini-3.6-flash",
            usage_metadata=types.SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0,
                                                 total_token_count=15))
        call = Mock(return_value=response)
        client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=call))
        with tempfile.TemporaryDirectory() as root, patch.dict(sys.modules, self.fake_types()):
            provider = GeminiProvider(config(), Path(root), D(1), client=client)
            p = provider.propose(DAY_MS, {})
            self.assertEqual(p.status, "invalid_response_flat_fallback")
            self.assertEqual(provider.propose(DAY_MS, {}), p)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(provider.ledger[p.input_hash]["status"], "settled")

    def test_unknown_outcome_reserves_budget_and_never_retries(self):
        call = Mock(side_effect=RuntimeError("sensitive detail must not propagate"))
        client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=call))
        with tempfile.TemporaryDirectory() as root, patch.dict(sys.modules, self.fake_types()):
            provider = GeminiProvider(config(), Path(root), D(1), client=client)
            for _ in range(2):
                with self.assertRaises(RuntimeError) as error:
                    provider.propose(DAY_MS, {})
                self.assertNotIn("sensitive detail", str(error.exception))
            self.assertEqual(call.call_count, 1)
            self.assertEqual(next(iter(provider.ledger.values()))["status"], "reserved_or_unknown")

    def test_budget_rejects_before_send(self):
        client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=Mock()))
        with tempfile.TemporaryDirectory() as root:
            provider = GeminiProvider(config(), Path(root), D(".01"), client=client)
            with self.assertRaises(RuntimeError):
                provider.propose(DAY_MS, {})
            client.models.generate_content.assert_not_called()

    def test_hard_ten_dollar_cap(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                GeminiProvider(config(), Path(root), D("10.01"), client=object())

    def test_settled_cost_and_unknown_reservation_both_count(self):
        client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=Mock()))
        with tempfile.TemporaryDirectory() as root:
            Path(root, "budget.json").write_text(json.dumps({
                "old-settled": {"status": "settled", "cost_or_reservation_usd": "9.91"},
                "old-unknown": {"status": "reserved_or_unknown", "cost_or_reservation_usd": "0.05"}}))
            provider = GeminiProvider(config(), Path(root), D(10), client=client)
            with self.assertRaises(RuntimeError):
                provider.propose(DAY_MS, {})
            client.models.generate_content.assert_not_called()

    def test_usage_settlement_releases_reservation_and_accumulates_tokens(self):
        response = types.SimpleNamespace(text=json.dumps({"weights": {"BTCUSDT": "0", "ETHUSDT": "0"}, "rationale": "flat"}),
            model_version="gemini-3.6-flash", usage_metadata=types.SimpleNamespace(
                prompt_token_count=1000, candidates_token_count=100, thoughts_token_count=20, total_token_count=1120))
        call = Mock(return_value=response)
        client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=call))
        with tempfile.TemporaryDirectory() as root, patch.dict(sys.modules, self.fake_types()):
            provider = GeminiProvider(config(), Path(root), D("0.0513"), client=client)
            first = provider.propose(DAY_MS, {})
            second = provider.propose(2 * DAY_MS, {})
            self.assertEqual(D(first.cost_usd), D(".0012"))
            self.assertEqual(sum(D(e["cost_or_reservation_usd"]) for e in provider.ledger.values()), D(".0024"))
            self.assertEqual(call.call_count, 2)
            self.assertEqual(provider.ledger[second.input_hash]["output_tokens_including_thinking"], 120)

    def test_negative_or_missing_usage_retains_reservation(self):
        response = types.SimpleNamespace(text="{}", usage_metadata=types.SimpleNamespace(
            prompt_token_count=None, candidates_token_count=10, thoughts_token_count=0))
        call = Mock(return_value=response)
        client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=call))
        with tempfile.TemporaryDirectory() as root, patch.dict(sys.modules, self.fake_types()):
            provider = GeminiProvider(config(), Path(root), D(1), client=client)
            with self.assertRaises(RuntimeError):
                provider.propose(DAY_MS, {})
            self.assertEqual(next(iter(provider.ledger.values()))["cost_or_reservation_usd"], "0.05")

    def test_competing_writer_rejected_and_exit_releases_lock(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "budget.lock"
            with budget_lock(path):
                with self.assertRaises(RuntimeError):
                    with budget_lock(path):
                        self.fail("second writer acquired lock")
            with budget_lock(path):
                pass


if __name__ == "__main__":
    unittest.main()
