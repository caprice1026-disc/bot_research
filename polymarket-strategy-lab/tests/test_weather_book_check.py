"""Archive reconstruction boundaries, including quiet-book age sensitivity."""
import json
import unittest

from weather_book_check import sample_books, usable


def event(ts, kind="book", bid=.4, ask=.6, change=None):
    if kind == "book":
        payload = {"bids": [{"price": str(bid), "size": "20"}],
                   "asks": [{"price": str(ask), "size": "30"}]}
    else:
        payload = {"price_change": change} if change else {}
    return {"recv_ts": ts, "source_event_ts": int((ts - .1) * 1e6),
        "source": "polymarket", "symbol": "token", "event_type": kind,
        "connection_id": "archive", "bid": bid, "ask": ask,
        "raw_payload": json.dumps(payload)}


class WeatherBookAuditTests(unittest.TestCase):
    def test_one_sided_ask_is_executable_but_absent_ask_is_not(self):
        row = event(100, bid=0, ask=.001)
        row["raw_payload"] = json.dumps({"bids": [], "asks": [{"price": ".001", "size": "15.73"}]})
        result, _ = sample_books([row], "token", 101, 1, allow_one_sided=True)
        self.assertTrue(usable(result["signal"], 60)[0])
        self.assertIsNone(result["signal"]["book_bid"])
        self.assertEqual(result["signal"]["book_ask_size"], 15.73)
        row["bid"], row["ask"] = .999, 1
        row["raw_payload"] = json.dumps({"bids": [{"price": ".999", "size": "15.73"}], "asks": []})
        result, _ = sample_books([row], "token", 101, 1, allow_one_sided=True)
        self.assertTrue(result["signal"]["book_book_valid"])
        self.assertEqual(usable(result["signal"], 60), (False, "no_ask_depth"))
        self.assertIsNone(result["signal"]["book_ask"])

    def test_actual_delta_restores_after_temporarily_crossed_book(self):
        rows = [event(10),
            event(20, "price_change", ask=.3, change={"asset_id": "token", "side": "SELL", "price": ".3", "size": "7"}),
            event(21, "price_change", bid=.2, ask=.3, change={"asset_id": "token", "side": "BUY", "price": ".4", "size": "0"}),
            event(22, "price_change", bid=.2, ask=.3, change={"asset_id": "token", "side": "BUY", "price": ".2", "size": "9"})]
        result, _ = sample_books(rows, "token", 21, 2, allow_one_sided=True)
        self.assertFalse(result["signal"]["book_book_valid"])
        self.assertTrue(result["execution"]["book_book_valid"])
        self.assertEqual(result["execution"]["book_ask_size"], 7)
        self.assertEqual(result["execution"]["book_bid_size"], 9)

    def test_bbo_before_delta_is_invalid_until_actual_delta_arrives(self):
        rows = [event(10), event(20, "best_bid_ask", ask=.55),
            event(20.001, "price_change", ask=.55, change={
                "asset_id": "token", "side": "SELL", "price": ".55", "size": "7"})]
        result, _ = sample_books(rows, "token", 20.001, 1)
        self.assertFalse(result["signal"]["book_book_valid"])
        self.assertTrue(result["execution"]["book_book_valid"])
        self.assertEqual(result["execution"]["book_ask"], .55)
        self.assertEqual(result["execution"]["book_ask_size"], 7)

    def test_quiet_book_remains_known_but_freshness_is_explicit(self):
        result, _ = sample_books([event(10), event(100, "best_bid_ask")], "token", 105, 60)
        self.assertTrue(result["signal"]["book_book_valid"])
        self.assertFalse(usable(result["signal"], 5)[0])  # source quote is 5.1 seconds old
        self.assertTrue(usable(result["signal"], 60)[0])
        self.assertFalse(usable(result["execution"], 60)[0])
        self.assertTrue(usable(result["execution"], 300)[0])

    def test_a_delta_without_initial_snapshot_never_creates_depth(self):
        delta = event(20, "price_change", change={
            "asset_id": "token", "side": "SELL", "price": ".6", "size": "7"})
        result, _ = sample_books([delta], "token", 21, 60)
        self.assertFalse(result["signal"]["book_book_valid"])
        self.assertIsNone(result["signal"]["book_ask_size"])
        self.assertEqual(result["signal"]["last_reported_quote"]["ask"], .6)


if __name__ == "__main__":
    unittest.main()
