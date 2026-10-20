"""Post-hoc size sensitivity at first snapshot; not an entry-rule retuning."""
import argparse
import csv
import json
from decimal import Decimal as D
from pathlib import Path
from evaluate_measured import GAS, write_csv

p = argparse.ArgumentParser()
p.add_argument('raw_json'); p.add_argument('fine_json'); p.add_argument('reference_csv'); p.add_argument('output_csv')
a = p.parse_args()
b = min(json.loads(Path(a.raw_json).read_text()).values(), key=lambda b: b['target_time'])
fine = json.loads(Path(a.fine_json).read_text())
with Path(a.reference_csv).open(encoding='utf-8-sig') as f:
    ref = next(r for r in csv.DictReader(f) if int(r['target_timestamp_ms'])//1000 == b['target_time'])
quotes = {D(n): (D(q['amount_out_raw'])/D(10)**18, q['quoter_gas_estimate']) for n,q in b['sizes'].items()}
for q in fine:
    assert q['block_number'] == b['block_number']
    quotes[D(q['amount_in_usdc'])] = (D(q['amount_out_jpyc']), q['quoter_gas_estimate'])
rows = []
for name,(overhead,floor,tip) in GAS.items():
    for n,(out,qgas) in sorted(quotes.items()):
        units = max(qgas+overhead,floor) if floor else 0
        gas = D(units)*(D(b['base_fee_per_gas_wei'])+D(tip)*10**9)/D(10)**18*D(ref['pol_reference_jpy'])
        cost=n*D(ref['usdc_reference_jpy'])
        rows.append(dict(scenario=name,timestamp=ref['target_time_utc'],block_number=b['block_number'],
                         amount_in_usdc=str(n),amount_out_jpyc=str(out),input_reference_jpy=str(cost),
                         assumed_swap_gas_jpy=str(gas),net_reference_profit_jpy=str(out-cost-gas),
                         profitable=out-cost-gas>0,
                         additional_price_impact_vs_1usdc_linear_jpyc=str(n*quotes[D(1)][0]-out),
                         classification='post_hoc_first_snapshot_size_sensitivity_not_strategy_retuning'))
write_csv(Path(a.output_csv),rows)
print(json.dumps([r for r in rows if D(r['amount_in_usdc'])<=5],indent=2))
