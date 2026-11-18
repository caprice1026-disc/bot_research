"""Offline contract tests: synthetic inputs, production replay functions."""
from collections import Counter
from decimal import Decimal as D
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "polymarket-strategy-lab"), str(ROOT / "trading-core/src")]
from btc_backtest import execute_leg, fee, replay
from btc_data import Book
from trading_core.execution.position_plan import ExecutionCosts
from trading_core.market_data.models import MarketSnapshot, MarketDataError

CONFIG = json.loads((ROOT / "polymarket-strategy-lab/btc_config.json").read_text())

def row(ts):
    r = dict(decision_ts=ts, slug="fixture", window_start_ts=0, window_end_ts=300)
    for side in ("up", "down"):
        r.update({f"{side}_bid": .43, f"{side}_ask": .44, f"{side}_ask_size": 10,
                  f"{side}_quote_age_seconds": .1, f"{side}_book_valid": True,
                  f"{side}_quote_source_ts": ts-.1, f"{side}_token_id": side})
    return r

def run(rows, **config):
    return replay(rows, {"fixture": {"winner_token": "down", "min_order_size": 5}},
                  "complete_set", dict(CONFIG, **config))

class ExecutionBounds(unittest.TestCase):
    def test_deeper_levels_are_not_swept_and_no_partial_fill_is_invented(self):
        book = Book()
        book.update(dict(event_type="book", recv_ts=9.9, connection_id="a",
            source_event_ts=9900000, bid=.43, ask=.44,
            raw_payload=json.dumps({"bids":[{"price":".43","size":"10"}],
                "asks":[{"price":".44","size":"5"},{"price":".45","size":"100"}]})), Counter())
        r = row(10)
        r.update(book.features("up", 10))
        self.assertTrue(book.valid)
        self.assertEqual(sum(book.asks.values()), 105)
        self.assertIsNone(execute_leg(r, "up", .50, 6, CONFIG))
        self.assertEqual(execute_leg(r, "up", .50, 5, CONFIG)["shares"], 5)

    def test_limit_is_preserved_after_adverse_slippage(self):
        self.assertIsNone(execute_leg(row(10), "up", .449, 5, CONFIG))
        self.assertAlmostEqual(execute_leg(row(10), "up", .45, 5, CONFIG)["price"], .45)

    def test_short_liquidity_during_latency_does_not_fill_first_leg(self):
        rows = [row(t) for t in (10,11,12)]
        rows[1]["up_ask_size"] = 5
        trades, diagnostics = run(rows)
        self.assertEqual(trades, [])
        self.assertEqual(diagnostics["unfilled"], 1)

    def test_latency_can_leave_unhedged_first_leg(self):
        rows = [row(t) for t in (10,11,12)]
        rows[2]["down_ask"] = .60
        trades, _ = run(rows)
        self.assertEqual([l["fill_ts"] for l in trades[0]["legs"]], [11])
        self.assertTrue(trades[0]["partial_pair"])
        self.assertEqual(trades[0]["payout"], 0)
        self.assertAlmostEqual(trades[0]["pnl"], -4.67325)

    def test_binary_settlement_and_collateral_fees_hand_calculated(self):
        trades, diag = run([row(t) for t in (10,11,12)])
        t = trades[0]
        # Each 10-share leg: 10*.45 + round(10*.07*.45*.55, 5) = 4.67325.
        self.assertEqual(fee(10, .45, .07), .17325)
        self.assertAlmostEqual(t["cost"], 9.34650)
        self.assertEqual(t["payout"], 10)
        self.assertAlmostEqual(t["pnl"], .65350)
        self.assertEqual(t["cash_release_ts"], 3900)
        self.assertAlmostEqual(diag["ending_cash_before_final_settlement"], 990.65350)

    def test_settlement_cash_is_not_reusable_before_release(self):
        before, db = run([row(t) for t in (10,11,12,3899)])
        after, da = run([row(t) for t in (10,11,12,3900)])
        self.assertEqual(before, after)
        self.assertAlmostEqual(da["ending_cash_before_final_settlement"]
                               - db["ending_cash_before_final_settlement"], 10)

    def test_replay_is_deterministic_and_does_not_mutate_input(self):
        rows = [row(t) for t in (10,11,12)]
        original = copy.deepcopy(rows)
        def digest():
            return hashlib.sha256(json.dumps(run(rows), sort_keys=True,
                                allow_nan=False).encode()).hexdigest()
        self.assertEqual(digest(), digest())
        self.assertEqual(rows, original)

    def test_perp_fixed_bps_model_is_not_a_depth_model(self):
        costs = ExecutionCosts(fee_rate=D(".001"), spread_bps=D("2"), slippage_bps=D("1"))
        self.assertEqual(costs.execution_price(D("100"), signed_delta=D("1")), D("100.02"))
        self.assertEqual(costs.execution_price(D("100"), signed_delta=D("1000")), D("100.02"))
        self.assertEqual(costs.execution_price(D("100"), signed_delta=D("-1")), D("99.98"))

    def test_common_snapshot_rejects_future_available_data(self):
        with self.assertRaises(MarketDataError):
            MarketSnapshot(as_of_ms=100, available_at_ms=101, mark_price=D("100"), quantity_step=D(".01"))

if __name__ == "__main__":
    unittest.main()
