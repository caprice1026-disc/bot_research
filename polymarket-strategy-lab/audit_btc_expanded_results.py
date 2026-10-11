"""Independent accounting and causality checks for saved expanded BTC results."""
import csv
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

ROOT = Path(__file__).resolve().parent


def fee(q, p):
    return float((Decimal(str(q)) * Decimal(str(p)) * (1 - Decimal(str(p))) * Decimal('.07'))
                 .quantize(Decimal('.00001'), rounding=ROUND_HALF_UP))


def near(a, b):
    return math.isclose(a, b, abs_tol=1e-6, rel_tol=1e-12)


def main():
    output = ROOT / 'results/btc_expanded'
    report_bytes = (output / 'summary.json').read_bytes()
    report = json.loads(report_bytes)
    with (ROOT / 'data/btc_expanded_replay.csv').open(newline='') as handle:
        rows = {float(r['decision_ts']): r for r in csv.DictReader(handle)}
    assert hashlib.sha256((ROOT / 'data/btc_expanded_replay.csv').read_bytes()).hexdigest() == report['data_sha256']
    assert hashlib.sha256((ROOT / 'btc_config.json').read_bytes()).hexdigest() == report['config_sha256']
    split, checked, audits = report['split_ts'], 0, {}
    for name, expected in report['strategies'].items():
        audit = {}
        for mode, latency, slip in [('base', 1, .01), ('stress', 2, .02)]:
            trades = json.loads((output / f'{name}_{mode}_trades.json').read_text())
            assert len({t['slug'] for t in trades}) == len(trades)
            cash, opened = 1000, []
            for trade in trades:
                checked += 1
                assert latency <= trade['fill_ts'] - trade['signal_ts'] <= latency + 1.1
                row = rows[trade['fill_ts']]
                assert row['slug'] == trade['slug']
                for old in list(opened):
                    if old['cash_release_ts'] <= trade['fill_ts']:
                        cash += old['payout']
                        opened.remove(old)
                settled = json.loads((ROOT / 'data/btc_settlements' / f"{trade['slug']}.json").read_text())
                winners = [t['token_id'] for t in settled['clob']['tokens'] if t.get('winner')]
                assert len(winners) == 1
                for leg in trade['legs']:
                    side, q, p = leg['side'], leg['shares'], leg['price']
                    assert row[f'{side}_book_valid'] == 'True'
                    assert float(row[f'{side}_quote_recv_ts']) < trade['fill_ts']
                    assert 0 <= float(row[f'{side}_book_snapshot_age_seconds']) <= 1
                    assert float(row[f'{side}_ask_size']) >= q >= 5
                    assert near(p, float(row[f'{side}_ask']) + slip)
                    assert p <= float(rows[trade['signal_ts']][f'{side}_ask']) + slip + 1e-12
                    assert near(leg['fee'], fee(q, p)) and leg['token'] == row[f'{side}_token_id']
                cost = sum(l['shares'] * l['price'] + fee(l['shares'], l['price']) for l in trade['legs'])
                payout = sum(l['shares'] for l in trade['legs'] if l['token'] == winners[0])
                assert near(trade['cost'], cost) and near(trade['payout'], payout) and near(trade['pnl'], payout - cost)
                assert trade['cash_release_ts'] >= float(row['window_end_ts']) + 3600
                cash -= cost
                assert near(cash, trade['cash_after_entry'])
                opened.append(trade)
            holdout = [t for t in trades if t['signal_ts'] >= split]
            for key, subset in [('all', trades), ('first_half', [t for t in trades if t['signal_ts'] < split]), ('second_half', holdout)]:
                assert expected[mode][key]['trades'] == len(subset)
                for field in ['cost', 'pnl', 'fees']:
                    assert near(sum(t[field] for t in subset), expected[mode][key]['net_pnl' if field == 'pnl' else field])
            daily = {f'2026-09-{day:02d}': 0.0 for day in range(16, 31)}
            for trade in holdout:
                daily[datetime.fromtimestamp(trade['signal_ts'], timezone.utc).date().isoformat()] += trade['pnl']
            audit[mode] = {'holdout_daily_pnl': daily, 'positive_days': sum(v > 0 for v in daily.values()),
                           'negative_days': sum(v < 0 for v in daily.values()), 'zero_days': sum(v == 0 for v in daily.values()),
                           'average_entry_price': statistics.mean(l['price'] for t in holdout for l in t['legs']) if holdout else None,
                           'win_fraction': sum(t['payout'] > 0 for t in holdout) / len(holdout) if holdout else None}
            if mode == 'base':
                for label, selected in [('all', trades), ('holdout', holdout)]:
                    pnl = sum(t['payout'] - sum(l['shares'] * min(1, l['price'] + .01) + fee(l['shares'], min(1, l['price'] + .01)) for l in t['legs']) for t in selected)
                    assert near(pnl, expected[f'fixed_fill_cost_stress_{label}']['net_pnl'])
                rng, means, values = random.Random(731), [], list(daily.values())
                if holdout:
                    for _ in range(2000):
                        draw = []
                        for _ in range(5):
                            start = rng.randrange(15)
                            draw.extend(values[(start + i) % 15] for i in range(3))
                        means.append(statistics.mean(draw))
                    means.sort()
                    assert all(near(a, b) for a, b in zip([means[49], means[1949]], expected['holdout_day_bootstrap']['mean_daily_pnl_ci95']))
                else:
                    assert expected['holdout_day_bootstrap']['mean_daily_pnl_ci95'] is None
                audit['bootstrap_matches'] = audit['fixed_fill_stress_matches'] = True
        audits[name] = audit
    assert (output / 'summary.json').read_bytes() == report_bytes, 'Result changed during audit'
    result = {'status': 'passed', 'checked_at': datetime.now(timezone.utc).isoformat(),
              'summary_sha256': hashlib.sha256(report_bytes).hexdigest(), 'data_sha256': report['data_sha256'],
              'trades_independently_checked': checked, 'strategies': audits}
    (ROOT / 'data/btc_expanded_result_audit.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
