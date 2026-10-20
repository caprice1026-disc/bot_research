import sys,json,csv
from pathlib import Path
from decimal import Decimal as D
from collections import Counter,defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'stateful'))
import replay,run_redemption as rr,run_stateful as rs
B=replay.BASE; O=B/'profit_sources'
ev=replay.events(); p=replay.initial(); refs=rs.refs(); times=sorted(refs)
Q=D(2**96); rate=(D(p.sqrt)/Q)**2/D(10**12)
fx0=D(refs[times[0]]['usdc_reference_jpy'])
stats={}
for z,name in [(True,'USDC_to_JPYC'),(False,'JPYC_to_USDC')]:
 e=[x for x in ev if x['type']=='swap' and x['zero_for_one']==z]
 stats[name]={'count':len(e),'usdc_abs':str(sum(D(abs(x['amount0']))/10**6 for x in e)),'jpyc_abs':str(sum(D(abs(x['amount1']))/10**18 for x in e))}
swap=[x for x in ev if x['type']=='swap'];mods=[x for x in ev if x['type']=='modify']
stats['trader_net_usdc']=str(sum(D(x['amount0'])/10**6 for x in swap))
stats['trader_net_jpyc']=str(sum(D(x['amount1'])/10**18 for x in swap))
stats['lp_modify_count']={'add':sum(x['delta']>0 for x in mods),'remove':sum(x['delta']<0 for x in mods),'zero':sum(x['delta']==0 for x in mods)}
stats['initial']={'rate_jpyc_per_usdc':str(rate),'fx_jpy_per_usdc':str(fx0),'spot_jpy_per_jpyc':str(fx0/rate),'spot_deviation_pct':str((fx0/rate-1)*100),'virtual_usdc':str(D(p.liquidity)*Q/D(p.sqrt)/10**6),'virtual_jpyc':str(D(p.liquidity)*D(p.sqrt)/Q/10**18),'active_liquidity_raw':p.liquidity}
orig_ev=rr.events;orig_ref=rr.refs
abl=[]
for mode in ['full','no_external_swaps','no_lp_changes','no_external_events','constant_fx','no_external_events_constant_fx']:
 rr.events=lambda: [x for x in ev if (x['type']!='swap' or mode not in ('no_external_swaps','no_external_events','no_external_events_constant_fx')) and (x['type']!='modify' or mode not in ('no_lp_changes','no_external_events','no_external_events_constant_fx'))]
 def getrefs():
  r={t:dict(v) for t,v in refs.items()}
  if mode in ('constant_fx','no_external_events_constant_fx'):
   for v in r.values():v['usdc_reference_jpy']=str(fx0)
  return r
 rr.refs=getrefs
 r=rr.simulate_redemption(1000,'1000','base',0,True,False)
 abl.append({'mode':mode,**r})
rr.events=orig_ev;rr.refs=orig_ref
stats['ablations']=abl
(O/'analysis.json').write_text(json.dumps(stats,indent=2))
print(json.dumps(stats,indent=2))
# Measure counterfactual external-order outcomes, holding consumed input fixed.
orig_apply=rr.apply; ext=[]
def tracked_apply(pool,e):
 r=orig_apply(pool,e)
 if e['type']=='swap':
  t=max(t for t in times if t<=e['timestamp']);fx=D(refs[t]['usdc_reference_jpy'])
  diff=D(r['amount_out']-e['output_raw'])/(10**18 if e['zero_for_one'] else 10**6)
  val=diff if e['zero_for_one'] else diff*fx
  ext.append({'direction':'USDC_to_JPYC' if e['zero_for_one'] else 'JPYC_to_USDC','block':e['block'],'output_change_tokens':str(diff),'output_change_reference_jpy':str(val),'fee_raw':r['fee_raw']})
 return r
rr.apply=tracked_apply
r=rr.simulate_redemption(1000,'1000','base',0,True,False);rr.apply=orig_apply
stats['fixed_external_order_counterfactual_output_change']={}
for name in ('USDC_to_JPYC','JPYC_to_USDC'):
 rows=[x for x in ext if x['direction']==name]
 stats['fixed_external_order_counterfactual_output_change'][name]={'count':len(rows),'output_change_tokens':str(sum(D(x['output_change_tokens']) for x in rows)),'output_change_reference_jpy':str(sum(D(x['output_change_reference_jpy']) for x in rows))}
with open(O/'external_order_counterfactual.csv','w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(ext[0]));w.writeheader();w.writerows(ext)
# Pool price and volume timeline at contemporaneous reference prices.
raw=json.loads((B/'public_rpc/grid/quotes_raw.json').read_text());prices=[]
from v4_math import decode_slot
for q in sorted(raw.values(),key=lambda q:q['target_time']):
 s,_,_,_=decode_slot(q['slot0']);fx=D(refs[q['target_time']]['usdc_reference_jpy']);rate=(D(s)/Q)**2/10**12
 prices.append({'time':q['target_time'],'rate':str(rate),'fx':str(fx),'jpy_per_jpyc':str(fx/rate)})
stats['actual_price']={'min_jpy_per_jpyc':str(min(D(x['jpy_per_jpyc']) for x in prices)),'max_jpy_per_jpyc':str(max(D(x['jpy_per_jpyc']) for x in prices)),'last':prices[-1]}
stats['own_trade_volume_ratio_vs_observed_gross_usdc']=str(D(202000)/(D(stats['USDC_to_JPYC']['usdc_abs'])+D(stats['JPYC_to_USDC']['usdc_abs'])))
stats['own_trade_volume_ratio_vs_observed_usdc_buys_jpyc']=str(D(202000)/D(stats['USDC_to_JPYC']['usdc_abs']))
(O/'analysis.json').write_text(json.dumps(stats,indent=2))
print(json.dumps({k:stats[k] for k in ('fixed_external_order_counterfactual_output_change','actual_price','own_trade_volume_ratio_vs_observed_gross_usdc','own_trade_volume_ratio_vs_observed_usdc_buys_jpyc')},indent=2))

with open(O/'ablation_summary.csv','w',encoding='utf-8-sig',newline='') as f:
 rows=[{k:r[k] for k in ('mode','trade_count','total_swap_usdc','net_profit_assuming_all_one_yen_redemptions_jpy')} for r in abl]
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
