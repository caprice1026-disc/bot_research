"""Regression checks for economically important replay boundaries."""
from collections import Counter
import json
import unittest

from btc_data import Book, replay_rows


def event(ts, kind="book", token="up", bid=.4, ask=.6, connection="a", **extra):
    body = {"bids": [{"price": str(bid), "size": "20"}],
            "asks": [{"price": str(ask), "size": "30"}]}
    return {"recv_ts": ts, "event_type": kind, "symbol": token,
        "source": "polymarket", "source_event_ts": int((ts - .1) * 1e6),
        "connection_id": connection, "bid": bid, "ask": ask,
        "raw_payload": json.dumps({"payload": body}), **extra}


class ReplayBoundaryTests(unittest.TestCase):
    def test_decision_uses_strictly_prior_events_and_keeps_missing_reference(self):
        markets = [{"market_id": "m", "slug": "btc-updown-5m-0",
                    "up_token_id": "up", "down_token_id": "down",
                    "window_start_ts": 0, "window_end_ts": 300}]
        rows = list(replay_rows([event(.2), event(.3, token="down"),
            event(1, bid=.45), event(2.1, bid=.46)], markets, Counter()))
        self.assertEqual([r["decision_ts"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["up_bid"], .4)
        self.assertEqual(rows[1]["up_bid"], .45)
        self.assertIsNone(rows[0]["chainlink30_price"])
        self.assertLess(rows[1]["up_quote_recv_ts"], rows[1]["decision_ts"])

    def test_depth_delta_uses_the_current_token_and_quantity(self):
        b, counts = Book(), Counter()
        b.update(event(.2), counts)
        b.update(event(.3, "price_change", ask=.55,
            raw_payload=json.dumps({"price_change": {
                "asset_id": "up", "side": "SELL", "price": ".55", "size": "7"}})), counts)
        self.assertTrue(b.valid)
        self.assertEqual(b.features("up", 1)["up_ask_size"], 7)
        b.update(event(.4, "price_change", raw_payload=json.dumps({"price_change": {
            "asset_id": "up", "side": "SELL", "price": ".55", "size": "0"}})), counts)
        self.assertTrue(b.valid)
        self.assertEqual(b.top(), (.4, .6))

    def test_mismatch_and_connection_change_require_fresh_snapshot(self):
        b, counts = Book(), Counter()
        b.update(event(.2), counts)
        b.update(event(.3, "best_bid_ask", ask=.5), counts)
        self.assertFalse(b.valid)
        self.assertIsNone(b.features("up", 1)["up_ask"])
        b.update(event(.4), counts)
        self.assertTrue(b.valid)
        b.update(event(.5, "best_bid_ask", connection="b"), counts)
        self.assertFalse(b.valid)
        b.update(event(.6, connection="b"), counts)
        self.assertTrue(b.valid)

    def test_receive_gap_does_not_reuse_old_depth(self):
        b, counts = Book(), Counter()
        b.update(event(.2), counts)
        b.update(event(6, "best_bid_ask"), counts)
        self.assertFalse(b.valid)
        self.assertEqual(b.reason, "receive_gap")

    def test_out_of_order_receive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            list(replay_rows([event(2), event(1)], [], Counter()))


if __name__ == "__main__":
    unittest.main()
