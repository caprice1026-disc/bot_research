"""Offline FX attribution + strategy rerun, all real external events retained."""
import sys,json,csv
from pathlib import Path
from decimal import Decimal as D
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'stateful'))
import run_redemption as r
from replay import writecsv
actual_refs=r.refs()
raw=json.loads((r.BASE/'public_rpc/grid/quotes_raw.json').read_text())
times=[b['target_time'] for b in sorted(raw.values(),key=lambda x:x['block_number'])]
fx0=D(actual_refs[times[0]]['usdc_reference_jpy'])
fixed_refs={t:dict(v,usdc_reference_jpy=str(fx0)) for t,v in actual_refs.items()}
for t in times:
    assert fixed_refs[t]['pol_reference_jpy']==actual_refs[t]['pol_reference_jpy']
fxs=[D(actual_refs[t]['usdc_reference_jpy']) for t in times]
metadata={'start_utc':r.iso(times[0]),'end_utc':r.iso(times[-1]),'observations':len(times),'fx_start':str(fx0),'fx_end':str(fxs[-1]),'fx_min':str(min(fxs)),'fx_max':str(max(fxs)),'fx_end_minus_start_pct':str((fxs[-1]/fx0-1)*100),'fx_min_time_utc':r.iso(times[fxs.index(min(fxs))]),'fx_max_time_utc':r.iso(times[fxs.index(max(fxs))]),'reference':'CoinGecko usd-coin / JPY, backward aligned; not a pure USDJPY oracle','POLJPY':'actual in both cases','funding_transfer_fx_spread_cost':0,'external_orders_and_liquidity_events':'unchanged in both cases'}
allruns=[];comparisons=[];ledger=[]
for cap,policy in [(100,'100'),(100,'greedy_grid'),(1000,'100'),(1000,'1000'),(1000,'greedy_grid')]:
 for delay in (0,3600,86400):
  pair={}
  for mode,ref in [('actual_fx',actual_refs),('fixed_initial_fx',fixed_refs)]:
   r.refs=lambda ref=ref:ref
   r.OUT=HERE/mode;r.OUT.mkdir(exist_ok=True)
   s=r.simulate_redemption(cap,policy,'base',delay,True,True)
   pair[mode]=s;allruns.append(dict(fx_mode=mode,**s))
  a=pair['actual_fx'];b=pair['fixed_initial_fx']
  assert a['initial_swap_capital_jpy']==b['initial_swap_capital_jpy']
  prefix=f'capital_{cap}_policy_{policy}_reinvest_delay_{delay//3600}h_gas_base'
  with open(HERE/'actual_fx'/(prefix+'_trades.csv'),encoding='utf-8-sig') as f:trades=list(csv.DictReader(f))
  effect=D(0);settled_effect=D(0)
  for t in trades:
   # actual profit minus identical quantities/path with constant reference cost
   delta=D(t['size_usdc'])*(fx0-D(t['fx_jpy_per_usdc']))
   effect+=delta
   if t['settled_within_window']=='True':settled_effect+=delta
   ledger.append(dict(capital_usdc_equiv=cap,policy=policy,delay_hours=delay//3600,**t,fixed_fx_acquisition_cost_jpy=str(D(t['size_usdc'])*fx0),actual_minus_fixed_same_trade_profit_jpy=str(delta)))
  pa=D(a['net_profit_assuming_all_one_yen_redemptions_jpy']);pb=D(b['net_profit_assuming_all_one_yen_redemptions_jpy'])
  row=dict(capital_usdc_equiv=cap,initial_capital_jpy=a['initial_swap_capital_jpy'],policy=policy,delay_hours=delay//3600,actual_trades=a['trade_count'],fixed_trades=b['trade_count'],actual_profit_jpy=str(pa),fixed_rerun_profit_jpy=str(pb),actual_minus_fixed_rerun_jpy=str(pa-pb),actual_minus_fixed_rerun_pct_of_fixed_profit=str((pa-pb)/pb*100) if pb>0 else '',actual_same_trade_fixed_fx_profit_jpy=str(pa-effect),same_trade_fx_effect_jpy=str(effect),same_trade_fx_effect_settled_jpy=str(settled_effect),same_trade_fx_effect_pending_jpy=str(effect-settled_effect),strategy_path_interaction_jpy=str((pa-pb)-effect),actual_settled_profit_jpy=a['settled_lot_net_profit_jpy'],actual_pending_profit_jpy=a['pending_lot_reference_net_profit_jpy'],fixed_settled_profit_jpy=b['settled_lot_net_profit_jpy'],fixed_pending_profit_jpy=b['pending_lot_reference_net_profit_jpy'],actual_gas_jpy=a['total_external_gas_cost_jpy'],fixed_gas_jpy=b['total_external_gas_cost_jpy'])
  comparisons.append(row)
  print(cap,policy,delay//3600,'n',a['trade_count'],b['trade_count'],'profit',round(pa,2),round(pb,2),'diff',round(pa-pb,2),'same-trade',round(effect,2),flush=True)
r.refs=lambda:actual_refs
writecsv(HERE/'comparison.csv',comparisons);writecsv(HERE/'same_trade_attribution.csv',ledger);writecsv(HERE/'all_runs.csv',allruns)
(HERE/'summaries.json').write_text(json.dumps(allruns,indent=2));(HERE/'metadata.json').write_text(json.dumps(metadata,indent=2))
print(json.dumps(metadata,indent=2))
