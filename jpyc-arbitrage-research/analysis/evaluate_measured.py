"""Historical Quoter opportunity analysis, never cumulative realized PnL."""
import argparse
import csv
import json
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from paper_scan import scan

POOL = '0x4643e3152a6ea3957e087cefbba74a1c628aa643e6b8a0edfae23a5796dde012'
GAS = {'zero_gas_boundary': (0, 0, 0), 'base_assumption': (150000, 250000, 30),
       'high_cost_assumption': (300000, 500000, 100)}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def write_csv(path, rows):
    if rows:
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def run(raw_path, reference_path, output):
    raw = json.loads(Path(raw_path).read_text())
    with Path(reference_path).open(encoding='utf-8-sig', newline='') as f:
        refs = {int(r['target_timestamp_ms']) // 1000: r for r in csv.DictReader(f)
                if r['usdc_reference_jpy'] and r['pol_reference_jpy']}
    if len({b['target_time'] for b in raw.values()}) != len(raw):
        raise ValueError('Duplicate target timestamp')
    if any(b['target_time'] not in refs for b in raw.values()):
        raise ValueError('Missing target reference')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    audit = []; summaries = []; size_stats = []
    for name, (overhead, floor, tip) in GAS.items():
        rows = []
        for b in sorted(raw.values(), key=lambda b: b['target_time']):
            t = b['target_time']; ref = refs[t]
            if b['block_time'] > t: raise ValueError('Future block')
            for token in ('usdc', 'pol'):
                if int(ref[token + '_source_timestamp_ms']) > t * 1000:
                    raise ValueError('Future FX')
            if set(b['sizes']) != {'1', '10', '100', '1000'}:
                raise ValueError('Incomplete preset size grid')
            slot = b['slot0'][2:]
            protocol_packed = int(slot[128:192], 16)
            protocol_pips = protocol_packed & 4095  # currency0 -> currency1
            lp_pips = int(slot[192:256], 16)
            total_pips = protocol_pips + lp_pips - protocol_pips * lp_pips // 1000000
            for amount, q in b['sizes'].items():
                units = max(q['quoter_gas_estimate'] + overhead, floor) if floor else 0
                price = D(b['base_fee_per_gas_wei']) + D(tip) * D(10)**9
                gas = D(units) * price / D(10)**18 * D(ref['pol_reference_jpy'])
                row = dict(timestamp=iso(t), block_number=b['block_number'], pool_id=POOL,
                           amount_in_usdc=amount, amount_out_jpyc=str(D(q['amount_out_raw']) / D(10)**18),
                           fx_jpy_per_usdc=ref['usdc_reference_jpy'], gas_cost_jpy=str(gas),
                           source_kind='historical_pool_quote',
                           source='https://tenderly.rpc.polygon.community; historical eth_call v4 Quoter')
                rows.append(row)
                audit.append(dict(scenario=name, **row, block_time_utc=iso(b['block_time']),
                                  block_age_seconds=t-b['block_time'], quoter_gas_estimate=q['quoter_gas_estimate'],
                                  assumed_total_swap_gas=units, block_base_fee_per_gas_wei=b['base_fee_per_gas_wei'],
                                  assumed_priority_fee_gwei=tip, pol_jpy=ref['pol_reference_jpy'],
                                  protocol_fee_pips=protocol_pips, lp_fee_pips=lp_pips, combined_fee_pips=total_pips))
        for capital in ('100', '1000'):
            affordable = [r for r in rows if D(r['amount_in_usdc']) <= D(capital)]
            records, s = scan(affordable, capital_usdc=D(capital))
            selected = [r for r in records if r['conditional_selected_candidate']]
            best = max(records, key=lambda r: D(r['net_reference_profit_jpy'])) if records else None
            s.update(scenario=name, expected_snapshots=len(refs), actual_snapshots=len(raw),
                     dataset_complete=len(raw)==len(refs),
                     tested_sizes_usdc=sorted({r['amount_in_usdc'] for r in affordable}, key=D),
                     first_conditional_candidate=selected[0] if selected else None,
                     historical_largest_grid_quote_profit=best,
                     largest_quote_is_hindsight_description_not_entry_rule=True)
            s['window_start_utc'] = min((r['timestamp'] for r in records), default=None)
            s['window_end_utc'] = max((r['timestamp'] for r in records), default=None)
            for amount in s['tested_sizes_usdc']:
                group = [r for r in records if r['amount_in_usdc'] == amount]
                nets = [D(r['net_reference_profit_jpy']) for r in group]
                size_stats.append(dict(scenario=name, capital_usdc=capital, amount_in_usdc=amount,
                                       snapshots=len(group), positive_snapshots=sum(n > 0 for n in nets),
                                       minimum_net_reference_profit_jpy=str(min(nets)),
                                       maximum_net_reference_profit_jpy=str(max(nets))))
            summaries.append(s)
            write_csv(output / f'{name}_capital_{capital}.csv', records)
    write_csv(output / 'size_sensitivity.csv', size_stats)
    write_csv(output / 'measured_quote_audit.csv', audit)
    (output / 'scenario_summaries.json').write_text(json.dumps(summaries, indent=2))
    compact = [{k:s[k] for k in ('scenario','initial_capital_usdc','actual_snapshots','dataset_complete',
                'positive_snapshots','candidate_episodes','conditional_selected_candidates','locked_input_usdc')}
               for s in summaries]
    write_csv(output / 'scenario_summary.csv', compact)
    print(json.dumps(compact, indent=2))
    return summaries


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('raw_json'); p.add_argument('reference_csv'); p.add_argument('output_dir')
    a = p.parse_args(); run(a.raw_json, a.reference_csv, a.output_dir)
