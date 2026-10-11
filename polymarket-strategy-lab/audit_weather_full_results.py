"""Reconcile the saved whole-cohort weather study without rerunning its model."""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
import math
from pathlib import Path
import random
import re
import statistics
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data/weather'
OUTPUT = ROOT / 'results/weather_full_books'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def near(a, b):
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-6)


def fee(q, p, rate):
    return float((Decimal(str(q)) * Decimal(str(p)) * (1 - Decimal(str(p))) * Decimal(str(rate)))
                 .quantize(Decimal('.00001'), rounding=ROUND_HALF_UP))


def probability(title, mean, sigma):
    title = title.replace('°F', '')
    values = [int(v) for v in re.findall(r'-?\d+', title.replace('-', ' '))]
    if 'below' in title:
        lower, upper = -math.inf, values[0] + .5
    elif 'higher' in title:
        lower, upper = values[0] - .5, math.inf
    else:
        assert len(values) == 2 and values[0] <= values[1]
        lower, upper = values[0] - .5, values[1] + .5
    cdf = lambda x: (1 + math.erf((x - mean) / (sigma * math.sqrt(2)))) / 2
    return cdf(upper) - cdf(lower)


def executable(book, at, age, quantity):
    fields = ['book_ask', 'book_ask_size', 'book_quote_recv_ts', 'book_quote_source_ts']
    if not book['book_book_valid'] or book['timestamp'] != at:
        return False
    if any(book.get(k) is None or not math.isfinite(book[k]) for k in fields):
        return False
    bid, ask = book['book_bid'], book['book_ask']
    return (0 < ask < 1 and (bid is None or math.isfinite(bid) and 0 <= bid < ask)
            and book['book_quote_recv_ts'] < at
            and 0 <= at - book['book_quote_recv_ts'] <= age
            and 0 <= at - book['book_quote_source_ts'] <= age
            and book['book_ask_size'] >= quantity)


def main():
    report_bytes = (OUTPUT / 'summary.json').read_bytes()
    report = json.loads(report_bytes)
    cfg, fit = report['config'], report['calibration']
    assert report['untouched_holdout'] is False and report['live_execution_verified'] is False
    assert sha(OUTPUT / 'input_manifest.json') == report['input_manifest_sha256']
    inputs = read(OUTPUT / 'input_manifest.json')['files']
    for item in inputs:
        assert sha(ROOT / item['path']) == item['sha256'], item['path']
    collection = read(ROOT / 'research/weather_full_book_data/manifest.json')
    assert collection['complete'] and not collection['errors'] and collection['completed_days'] == 30
    assert collection['scope_sha256'] == sha(ROOT / 'research/weather_full_book_acquisition_config_v2.json')
    for day in collection['days']:
        for part in day['partitions']:
            assert sha(ROOT / part['subset_path']) == part['subset_sha256']
    snapshots_path = ROOT / collection['snapshots_path']
    assert sha(snapshots_path) == collection['snapshots_sha256'] == report['data_sha256']
    snapshots = {}
    for row in map(json.loads, snapshots_path.read_text(encoding='utf-8').splitlines()):
        key = (row['date'], row['condition_id'], row['outcome'])
        assert key not in snapshots
        snapshots[key] = row
    assert len(snapshots) == 660
    baseline = read(ROOT / 'research/weather_config.json')
    actuals = defaultdict(list)
    for obs in csv.DictReader(io.StringIO(read(DATA / 'observations_hourly.json')['body'])):
        if obs['station'] == 'ORD' and obs['metar'].startswith('KORD ') and obs['tmpf'] not in ('M', ''):
            at = datetime.strptime(obs['valid'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            ds = (at + timedelta(hours=baseline['utc_offset_hours'])).date().isoformat()
            actuals[ds].append(float(obs['tmpf']))
    forecasts, markets, expected_keys = {}, {}, set()
    for path in sorted((DATA / 'forecasts').glob('*.json')):
        ds = path.stem
        forecast = read(path)
        params = parse_qs(urlsplit(forecast['url']).query)
        assert params['run'] == [ds + 'T00:00']
        assert params['timezone'] == ['America/Chicago']
        assert forecast['body']['utc_offset_seconds'] == -18000
        hourly = forecast['body']['hourly']
        forecasts[ds] = {}
        for model in fit['weights']:
            values = [v for t, v in zip(hourly['time'], hourly['temperature_2m_' + model]) if t.startswith(ds)]
            assert len(values) == 24 and all(v is not None for v in values)
            forecasts[ds][model] = max(values)
        if not cfg['start_date'] <= ds <= cfg['end_date']:
            continue
        event = read(DATA / 'events' / (ds + '.json'))['body'][0]
        assert len(event['markets']) == 11
        for market in event['markets']:
            cid = market['conditionId']
            tokens = dict(zip(json.loads(market['outcomes']), json.loads(market['clobTokenIds'])))
            resolved = [r for r in read(DATA / 'resolutions' / (cid + '.json'))['body']['data']
                        if r['condition_id'] == cid and r['status'] == 'resolved']
            assert len(resolved) == 1 and resolved[0]['transaction_hash']
            resolution = resolved[0]
            close = datetime.fromisoformat(market['closedTime'].replace('Z', '+00:00')).timestamp()
            markets[(ds, cid)] = (market, resolution, min(close, int(resolution['last_update_timestamp'])))
            for outcome, token in tokens.items():
                key = (ds, cid, outcome)
                expected_keys.add(key)
                assert snapshots[key]['token_id'] == str(token)
    assert expected_keys == set(snapshots)
    calibration_days = sorted(ds for ds in forecasts if baseline['start_date'] <= ds <= baseline['calibration_end'])
    assert len(calibration_days) == fit['days'] == 30 and fit['through'] < cfg['start_date']
    errors = {model: statistics.mean(abs(math.floor(max(actuals[ds]) + .5) - forecasts[ds][model]) for ds in calibration_days)
              for model in fit['weights']}
    inverse = {model: 1 / max(1, value) for model, value in errors.items()}
    weights = {model: value / sum(inverse.values()) for model, value in inverse.items()}
    residuals = [math.floor(max(actuals[ds]) + .5) - sum(weights[m] * forecasts[ds][m] for m in weights) for ds in calibration_days]
    assert all(near(weights[m], fit['weights'][m]) for m in weights)
    assert near(statistics.mean(residuals), fit['bias_f'])
    assert near(max(baseline['sigma_floor_fahrenheit'], statistics.stdev(residuals)), fit['sigma_f'])
    summaries, checked_fills, checked_candidates = {}, 0, 0
    for name, expected in report['scenarios'].items():
        path = OUTPUT / expected['result_file']
        assert sha(path) == expected['result_sha256']
        result = read(path)
        age, markup, quantity, rate = expected['max_quote_age_seconds'], expected['price_markup'], cfg['order_shares'], cfg['current_taker_fee_rate']
        by_date = defaultdict(list)
        candidate_keys = []
        for candidate in result['candidates']:
            checked_candidates += 1
            ds, cid, outcome = candidate['date'], candidate['condition_id'], candidate['outcome']
            key = (ds, cid, outcome)
            candidate_keys.append(key)
            snapshot = snapshots[key]
            market, resolution, stop = markets[(ds, cid)]
            decision = datetime.fromisoformat(ds).replace(hour=15, tzinfo=timezone.utc).timestamp()
            assert candidate['decision_timestamp'] == decision
            assert decision >= datetime.fromisoformat(ds).replace(tzinfo=timezone.utc).timestamp() + baseline['forecast_release_lag_hours'] * 3600
            mean = sum(fit['weights'][m] * forecasts[ds][m] for m in fit['weights']) + fit['bias_f']
            yes = probability(market['groupItemTitle'], mean, fit['sigma_f'])
            modeled_probability = yes if outcome == 'Yes' else 1 - yes
            assert near(mean, candidate['forecast_mu_f']) and near(modeled_probability, candidate['model_probability'])
            valid = decision < stop and executable(snapshot['signal'], decision, age, quantity)
            price = round(snapshot['signal']['book_ask'] + markup, 10) if valid else None
            edge = modeled_probability - price - fee(quantity, price, rate) / quantity if valid and price < 1 else None
            eligible = edge is not None and edge >= cfg['forecast_min_net_edge']
            assert eligible == (candidate['signal_reason'] == 'eligible')
            if edge is not None:
                assert near(edge, candidate['estimated_net_edge'])
            if eligible:
                by_date[ds].append(candidate)
        assert len(candidate_keys) == 660 and set(candidate_keys) == expected_keys
        chosen = [max(values, key=lambda c: (c['estimated_net_edge'], c['condition_id'], c['outcome'])) for values in by_date.values()]
        choices = {(c['date'], c['condition_id'], c['outcome']) for c in chosen}
        assert choices == {(c['date'], c['condition_id'], c['outcome']) for c in result['candidates'] if c['selected']}
        assert choices == {(a['date'], a['condition_id'], a['outcome']) for a in result['attempts']}
        fills_by_date = {f['date']: f for f in result['fills']}
        assert len(fills_by_date) == len(result['fills'])
        for attempt in result['attempts']:
            key = (attempt['date'], attempt['condition_id'], attempt['outcome'])
            snapshot = snapshots[key]
            at = attempt['decision_timestamp'] + cfg['execution_latency_seconds']
            market, resolution, stop = markets[key[:2]]
            valid = at < stop and executable(snapshot['execution'], at, age, quantity)
            price = round(snapshot['execution']['book_ask'] + markup, 10) if valid else None
            should_fill = valid and price < 1 and price <= snapshot['signal']['book_ask'] + cfg['max_price_move'] + 1e-10
            assert should_fill == (attempt['date'] in fills_by_date)
            if not should_fill:
                continue
            checked_fills += 1
            fill = fills_by_date[attempt['date']]
            assert fill['condition_id'] == key[1] and fill['outcome'] == key[2] and fill['token_id'] == snapshot['token_id']
            settlement = int(resolution['price']) / 10**18
            if key[2] == 'No':
                settlement = 1 - settlement
            expected_fee = fee(quantity, price, rate)
            cost, payout = quantity * price + expected_fee, quantity * settlement
            for field, value in [('entry_price', price), ('shares', quantity), ('fees', expected_fee), ('cost', cost), ('settlement', settlement), ('payout', payout), ('pnl', payout - cost)]:
                assert near(fill[field], value), (name, field, fill['date'])
            assert fill['fill_timestamp'] == at < stop
            assert fill['resolution_timestamp'] == int(resolution['last_update_timestamp'])
            assert fill['resolution_transaction_hash'] == resolution['transaction_hash']
        cash, reserve, minimum, maximum, ledger = cfg['initial_capital'], 0, cfg['initial_capital'], 0, []
        for fill in result['fills']:
            ledger.extend([(fill['fill_timestamp'], 1, -fill['cost'], fill['cost'], fill),
                           (fill['resolution_timestamp'], 0, fill['payout'], -fill['cost'], fill)])
        for _, kind, change, reserved_change, fill in sorted(ledger, key=lambda x: x[:2]):
            if kind == 1:
                assert near(cash, fill['cash_before_entry'])
            cash += change
            reserve += reserved_change
            minimum, maximum = min(minimum, cash), max(maximum, reserve)
            assert cash >= 0 and reserve >= -1e-8
            if kind == 1:
                assert near(cash, fill['cash_after_entry'])
        daily = {d['date']: d['pnl'] for d in result['daily']}
        assert len(daily) == 30
        assert all(near(value, fills_by_date[ds]['pnl'] if ds in fills_by_date else 0) for ds, value in daily.items())
        summary = result['summary']
        assert summary['fills'] == len(result['fills']) and summary['attempts'] == len(result['attempts'])
        for field, value in [('net_pnl', sum(daily.values())), ('ending_cash', cash), ('minimum_cash', minimum), ('maximum_reserved_cost', maximum),
                             ('deployed_cost', sum(f['cost'] for f in result['fills'])), ('fees', sum(f['fees'] for f in result['fills']))]:
            assert near(summary[field], value) and near(expected[field], value)
        rng, values, bootstrap = random.Random(cfg['bootstrap_seed']), list(daily.values()), []
        if result['fills']:
            for _ in range(cfg['bootstrap_samples']):
                draw = []
                for _ in range(10):
                    start = rng.randrange(30)
                    draw.extend(values[(start + offset) % 30] for offset in range(3))
                bootstrap.append(sum(draw))
            bootstrap.sort()
            assert all(near(a, b) for a, b in zip([bootstrap[50], bootstrap[1950]], summary['three_day_block_bootstrap_pnl_95pct']))
        else:
            assert summary['three_day_block_bootstrap_pnl_95pct'] == [None, None]
        fixed_stress = {}
        if markup == cfg['base_price_markup']:
            for stress in cfg['stress_price_markups']:
                pnl = sum(f['payout'] - quantity * min(1, round(f['entry_price'] + stress - markup, 10))
                          - fee(quantity, min(1, round(f['entry_price'] + stress - markup, 10)), rate) for f in result['fills'])
                fixed_stress[str(stress)] = round(pnl, 6)
        summaries[name] = {'fills': len(result['fills']), 'pnl': summary['net_pnl'], 'fixed_same_fill_cost_stress': fixed_stress,
                           'positive_days': sum(v > 0 for v in values), 'negative_days': sum(v < 0 for v in values),
                           'zero_days': sum(v == 0 for v in values), 'maximum_single_day_profit': max(values),
                           'outcomes': dict(Counter(f['outcome'] for f in result['fills']))}
    assert (OUTPUT / 'summary.json').read_bytes() == report_bytes, 'Result changed during audit'
    audit = {'status': 'passed', 'checked_at': datetime.now(timezone.utc).isoformat(),
             'summary_sha256': hashlib.sha256(report_bytes).hexdigest(), 'input_hashes_verified': len(inputs),
             'candidate_calculations_checked': checked_candidates, 'fills_checked': checked_fills,
             'calibration_independently_recomputed': True, 'scenarios': summaries}
    (ROOT / 'data/weather_full_result_audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
