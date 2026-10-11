import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from btc_backtest import fee, quote_valid, replay, signal


class ReplaySafety(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'btc_config.json').read_text())
        self.settlements = {'example': {'winner_token': 'down-token', 'min_order_size': 5}}

    def row(self, ts):
        row = dict(decision_ts=ts, slug='example', window_start_ts=0, window_end_ts=300,
                   coinbase_price=100, coinbase_age_seconds=0, coinbase_source_ts=ts-.1,
                   chainlink60_price=100, chainlink60_source_ts=ts-.1)
        for side in ('up', 'down'):
            row.update({f'{side}_bid': 0.43, f'{side}_ask': 0.44,
                        f'{side}_bid_size': 100, f'{side}_ask_size': 100,
                        f'{side}_quote_age_seconds': 0.1, f'{side}_book_valid': True,
                        f'{side}_quote_source_ts': ts-.1,
                        f'{side}_token_id': side + '-token'})
        return row

    def test_current_fee_and_invalid_inputs(self):
        self.assertEqual(fee(100, .5, .07), 1.75)
        self.assertEqual(fee(100, .3, .05), 1.05)
        for bad in (float('nan'), -1, 2):
            with self.assertRaises(ValueError):
                fee(5, bad, .07)

    def test_stale_crossed_or_insufficient_size_cannot_fill(self):
        row = self.row(10)
        for key, value in [('up_quote_age_seconds', 2.01), ('up_bid', .9),
                           ('up_ask_size', 4.9), ('up_book_valid', False)]:
            invalid = dict(row, **{key: value})
            self.assertFalse(quote_valid(invalid, 'up', self.config))

    def test_two_legs_have_separate_delays_and_cash(self):
        rows = [self.row(t) for t in range(10, 15)]
        trades, _ = replay(rows, self.settlements, 'complete_set', self.config)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual([leg['fill_ts'] for leg in trade['legs']], [11, 12])
        self.assertFalse(trade['partial_pair'])
        self.assertAlmostEqual(trade['cash_after_entry'], 1000 - trade['cost'])
        self.assertAlmostEqual(trade['pnl'], trade['payout'] - trade['cost'])

    def test_missing_second_leg_records_directional_loss(self):
        for final_row in (None, self.row(16), dict(self.row(12), down_ask=.8)):
            rows = [self.row(10), self.row(11)]
            if final_row:
                rows.append(final_row)
            trades, _ = replay(rows, self.settlements, 'complete_set', self.config)
            self.assertEqual(len(trades), 1)
            self.assertTrue(trades[0]['partial_pair'])
            self.assertLess(trades[0]['pnl'], 0)
            self.assertEqual(trades[0]['payout'], 0)

    def test_signal_does_not_use_settlement_winner(self):
        rows = [self.row(t) for t in range(10, 14)]
        changed = copy.deepcopy(self.settlements)
        changed['example']['winner_token'] = 'up-token'
        a, _ = replay(rows, self.settlements, 'complete_set', self.config)
        b, _ = replay(rows, changed, 'complete_set', self.config)
        self.assertEqual(a[0]['legs'], b[0]['legs'])

    def test_future_price_spike_cannot_modify_prior_signal(self):
        rows = [self.row(t) for t in range(0, 150)]
        for t, row in enumerate(rows):
            row['coinbase_price'] = 100 + .002 * t + .01 * (t % 3)
        times = [r['decision_ts'] for r in rows]
        before = signal('btc_fair_value', rows, times, rows[-1], self.config)
        altered = rows + [dict(self.row(151), coinbase_price=100000)]
        after = signal('btc_fair_value', altered, times + [151], rows[-1], self.config)
        self.assertEqual(before, after)

    def test_minimum_shares_cash_cap(self):
        config = dict(self.config, initial_cash=1)
        trades, diagnostics = replay([self.row(t) for t in range(10, 15)], self.settlements, 'complete_set', config)
        self.assertEqual(trades, [])
        self.assertGreater(diagnostics['cash_or_minimum_size'], 0)

    def test_fresh_bbo_cannot_refresh_old_snapshot_quantity(self):
        row = dict(self.row(10), up_book_snapshot_age_seconds=5)
        self.assertTrue(quote_valid(row, 'up', self.config))
        self.assertFalse(quote_valid(row, 'up', dict(self.config, max_book_snapshot_age_seconds=1)))


if __name__ == '__main__':
    unittest.main()
