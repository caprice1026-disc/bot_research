"""Regression and accounting checks for generated FX sensitivity output."""
import json,csv
from pathlib import Path
from decimal import Decimal as D, getcontext
getcontext().prec=65
P=Path(__file__).resolve().parent
runs=json.loads((P/'summaries.json').read_text())
original=json.loads((P.parent/'stateful/redemption_results/summaries.json').read_text())
def key(r):return (r['capital_usdc_equiv'],r['policy'],r['gas_scenario'],r['reinvest'],r['redemption_delay_hours'])
orig={key(r):r for r in original};n=0
for r in runs:
 if r['fx_mode']=='actual_fx':
  assert {k:v for k,v in r.items() if k!='fx_mode'}==orig[key(r)];n+=1
 assert abs(D(r['net_profit_assuming_all_one_yen_redemptions_jpy'])-D(r['settled_lot_net_profit_jpy'])-D(r['pending_lot_reference_net_profit_jpy']))<D('1e-40')
 assert abs(D(r['net_nav_after_gas_jpy'])-D(r['initial_swap_capital_jpy'])-D(r['net_profit_assuming_all_one_yen_redemptions_jpy']))<D('1e-40')
 assert D(r['ending_cash_before_external_gas_jpy'])>=0
with open(P/'comparison.csv',encoding='utf-8-sig') as f:cs=list(csv.DictReader(f))
for c in cs:
 assert abs(D(c['actual_minus_fixed_rerun_jpy'])-D(c['same_trade_fx_effect_jpy'])-D(c['strategy_path_interaction_jpy']))<D('1e-40')
 assert abs(D(c['actual_profit_jpy'])-D(c['actual_same_trade_fixed_fx_profit_jpy'])-D(c['same_trade_fx_effect_jpy']))<D('1e-40')
print(f'PASS: {n} actual runs exactly match prior published runs; {len(runs)} NAV/settled/pending/cash checks; {len(cs)} paired attribution identities.')
