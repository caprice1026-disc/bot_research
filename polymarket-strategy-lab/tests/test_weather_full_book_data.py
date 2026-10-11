"""Acquisition safeguards: all markets, historical seeds, and causal boundaries."""
from datetime import date, datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import weather_full_book_data as full
from test_weather_book_check import event


class FullWeatherBookTests(unittest.TestCase):
    def test_historically_closed_market_does_not_request_old_seed(self):
        day = date(2026, 8, 31)
        decision = int(datetime(2026, 8, 31, 15, tzinfo=timezone.utc).timestamp())
        markets = [{"condition_id": "condition", "bucket": "test", "tokens": {"Yes": "yes", "No": "no"},
            "trading_stop_timestamp": decision - 1, "current_min_order_shares_reference_only": 5}]
        with patch.object(full, "markets_for_day", return_value=(markets, "metadata-hash")), \
                patch.object(full, "subset", return_value=(Path("unused"), {"rows": 0})) as fetch, \
                patch.object(full, "archive_depth_events", return_value=[]):
            rows, _ = full.acquire_day(None, day, {"execution_latency_seconds": 60,
                "missing_snapshot_max_lookback_hours": 24})
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(all(r["ineligible_closed_at_decision"] for r in rows))
        self.assertTrue(all(r["signal"]["last_full_snapshot"] is None for r in rows))

    def test_future_snapshot_cannot_replace_missing_initial_seed(self):
        day = date(2026, 8, 31)
        decision = int(datetime(2026, 8, 31, 15, tzinfo=timezone.utc).timestamp())
        markets = [{"condition_id": "condition", "bucket": "test",
            "tokens": {"Yes": "yes", "No": "no"},
            "current_min_order_shares_reference_only": 5}]
        calls = []

        def fetch(con, ds, hour, conditions, start, end):
            calls.append((hour, conditions))
            return Path(str(hour)), {"rows": 2}

        def archive(con, path, ds):
            hour = int(str(path))
            if hour == 14:
                return []
            # Post-decision snapshots must not initialize the signal state.
            ts = decision + 30 if hour == 15 else decision - 3601
            rows = []
            for token in ("yes", "no"):
                row = event(ts, ask=.7 if hour == 15 else .6)
                row["symbol"] = token
                rows.append(row)
            return rows

        scope = {"execution_latency_seconds": 60, "missing_snapshot_max_lookback_hours": 24}
        with patch.object(full, "markets_for_day", return_value=(markets, "metadata-hash")), \
                patch.object(full, "subset", side_effect=fetch), \
                patch.object(full, "archive_depth_events", side_effect=archive):
            rows, meta = full.acquire_day(None, day, scope)
        self.assertEqual([c[0] for c in calls], [14, 15, 13])
        self.assertEqual(len(rows), 2)
        self.assertEqual(meta["outcome_count"], 2)
        for row in rows:
            self.assertEqual(row["signal"]["book_ask"], .6)
            self.assertEqual(row["execution"]["book_ask"], .7)
            self.assertLess(row["signal"]["book_quote_recv_ts"], decision)

    def test_existing_cohort_has_all_thirty_days_and_unique_token_mappings(self):
        dates = list(full.dates_between("2026-08-31", "2026-09-29"))
        seen = set()
        self.assertEqual(len(dates), 30)
        for day in dates:
            markets, digest = full.markets_for_day(day.isoformat())
            self.assertEqual(len(markets), 11)
            self.assertEqual(len(digest), 64)
            for market in markets:
                self.assertFalse(seen.intersection(market["tokens"].values()))
                seen.update(market["tokens"].values())
        self.assertEqual(len(seen), 660)

    def test_duplicate_token_mapping_is_rejected(self):
        market = {"conditionId": "condition", "outcomes": '["Yes","No"]',
                  "clobTokenIds": '["same","same"]'}
        fixture = {"body": [{"markets": [market] * 11}]}
        with patch.object(full, "read_json", return_value=fixture):
            with self.assertRaisesRegex(ValueError, "Duplicate condition/token"):
                full.markets_for_day("2026-08-31")


if __name__ == "__main__":
    unittest.main()
