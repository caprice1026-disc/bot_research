"""Finite-capital, no-recycling, fixed external gross-input replay experiment."""
import json,csv
from decimal import Decimal as D, getcontext
from pathlib import Path
from datetime import datetime,timezone
from replay import BASE,initial,events,apply,writecsv
from v4_math import decode_slot
getcontext().prec=65
OUT=BASE/'stateful/results';OUT.mkdir(exist_ok=True)
GAS={'base':(150000,250000,30),'high':(300000,500000,100)}
def iso(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
def refs():
    with open(BASE/'reference/seed_aligned_reference_prices.csv',encoding='utf-8-sig') as f:
        return {int(r['target_timestamp_ms'])//1000:r for r in csv.DictReader(f) if r['usdc_reference_jpy'] and r['pol_reference_jpy']}
def gas_cost(b,ref,profile,qgas=60000,steps=1):
    overhead,floor,tip=GAS[profile]
    # No transaction execution: only explicit gas scenario, no upper-bound guarantee.
    units=max(qgas+overhead+(steps-1)*10000,floor)
    return D(units)*(D(b['base_fee_per_gas_wei'])+D(tip)*10**9)/10**18*D(ref['pol_reference_jpy'])
def validate_grid():
    p=initial();ev=events();j=0;rows=[];raw=json.loads((BASE/'public_rpc/grid/quotes_raw.json').read_text())
    for b in sorted(raw.values(),key=lambda b:b['block_number']):
        while j<len(ev) and ev[j]['block']<=b['block_number']:apply(p,ev[j]);j+=1
        actual_s,actual_t,*_=decode_slot(b['slot0'])
        if p.tick!=actual_t or p.liquidity!=b['liquidity_raw']:raise AssertionError('baseline tick/liquidity mismatch')
        # State-reset here ONLY in the validation quote, never in counterfactual replay.
        observed=p.clone();observed.sqrt=actual_s;observed.tick=actual_t
        for size,q in b['sizes'].items():
            got=observed.clone().swap(int(size)*10**6)['amount_out']
            if got!=q['amount_out_raw']:raise AssertionError(f'Quoter mismatch {b["block_number"]} {size} {got-q["amount_out_raw"]}')
            replay_out=p.clone().swap(int(size)*10**6)['amount_out']
            rows.append(dict(block=b['block_number'],size_usdc=size,observed_state_quote_raw_error=got-q['amount_out_raw'],replay_state_output_error_jpy=str(D(replay_out-q['amount_out_raw'])/10**18),baseline_price_error_bps=str(((D(p.sqrt)/actual_s)**2-1)*10000)))
    writecsv(OUT/'quoter_validation.csv',rows)
    result=dict(quotes_checked=len(rows),observed_state_raw_exact_matches=sum(r['observed_state_quote_raw_error']==0 for r in rows),max_baseline_price_error_bps=str(max(abs(D(r['baseline_price_error_bps'])) for r in rows)),max_baseline_quote_error_jpy=str(max(abs(D(r['replay_state_output_error_jpy'])) for r in rows)),no_state_reset_in_baseline=True)
    (OUT/'validation.json').write_text(json.dumps(result,indent=2));return result

def simulate(capital,policy,profile):
    p=initial();ev=events();j=0;rs=refs();raw=json.loads((BASE/'public_rpc/grid/quotes_raw.json').read_text());snaps=sorted(raw.values(),key=lambda b:b['block_number'])
    cash=int(capital)*10**6;inventory=0;gas_total=D(0);ref_surplus=D(0);trades=[];decisions=[]
    initial_fx=D(rs[snaps[0]['target_time']]['usdc_reference_jpy'])
    for b in snaps:
        while j<len(ev) and ev[j]['block']<=b['block_number']:apply(p,ev[j]);j+=1
        r=rs[b['target_time']];fx=D(r['usdc_reference_jpy'])
        sizes=[1,10,100,1000] if policy=='greedy_grid' else [int(policy)]
        candidates=[]
        for s in sizes:
            amount=s*10**6
            if amount>cash:continue
            cp=p.clone();quote=cp.swap(amount);qgas=b['sizes'].get(str(s),{'quoter_gas_estimate':60000})['quoter_gas_estimate'];gas=gas_cost(b,r,profile,qgas,quote['steps'])
            net=D(quote['amount_out'])/10**18-D(amount)/10**6*fx-gas
            candidates.append((net,amount,cp,quote,gas))
        viable=[c for c in candidates if c[0]>0]
        action='skip_nonpositive' if candidates else 'no_affordable_grid_size'
        if viable:
            net,amount,cp,q,gas=max(viable,key=lambda c:(c[0],-c[1]));before=p.sqrt;p=cp
            cash-=amount;inventory+=q['amount_out'];gas_total+=gas;ref_surplus+=net;action='paper_buy'
            trades.append(dict(time_utc=iso(b['target_time']),block=b['block_number'],amount_in_usdc=str(D(amount)/10**6),amount_out_jpyc=str(D(q['amount_out'])/10**18),fx_jpy_per_usdc=str(fx),gas_cost_jpy=str(gas),reference_surplus_jpy=str(net),cash_remaining_usdc=str(D(cash)/10**6),inventory_jpyc=str(D(inventory)/10**18),sqrt_before=str(before),sqrt_after=str(p.sqrt),tick_after=p.tick,liquidity_after=str(p.liquidity),steps=q['steps']))
        decisions.append(dict(time_utc=iso(b['target_time']),block=b['block_number'],action=action,cash_usdc=str(D(cash)/10**6),inventory_jpyc=str(D(inventory)/10**18),counterfactual_sqrt=str(p.sqrt),tick=p.tick,active_liquidity=str(p.liquidity)))
    assert j==len(ev),'unprocessed end events'
    end=snaps[-1];ref=rs[end['target_time']];fx=D(ref['usdc_reference_jpy'])
    # Separate optional terminal DEX sale, not the strategy's actual action.
    if inventory:
        exitquote=p.clone().swap(inventory,False);exit_gas=gas_cost(end,ref,profile,60000,exitquote['steps']);sale_usdc=D(exitquote['amount_out'])/10**6
    else:sale_usdc=D(0);exit_gas=D(0)
    cash_usdc=D(cash)/10**6;inv=D(inventory)/10**18
    result=dict(capital_usdc=capital,policy=policy,gas_scenario=profile,snapshots=len(snaps),trade_count=len(trades),spent_usdc=str(D(capital)-cash_usdc),ending_cash_usdc=str(cash_usdc),ending_inventory_jpyc=str(inv),gas_total_jpy=str(gas_total),acquisition_reference_surplus_jpy=str(ref_surplus),ending_one_yen_valuation_vs_initial_jpy=str(cash_usdc*fx+inv-gas_total-D(capital)*initial_fx),ending_one_yen_valuation_vs_hold_usdc_jpy=str(inv-(D(capital)-cash_usdc)*fx-gas_total),hypothetical_terminal_dex_sale_usdc=str(sale_usdc),hypothetical_terminal_dex_sale_gas_jpy=str(exit_gas),hypothetical_dex_exit_vs_hold_usdc_jpy=str((cash_usdc+sale_usdc-D(capital))*fx-gas_total-exit_gas),no_redemption_or_cash_recycling=True,external_orders='fixed_observed_gross_input',realized_profit=None,first_trade_utc=trades[0]['time_utc'] if trades else None,last_trade_utc=trades[-1]['time_utc'] if trades else None)
    prefix=f'capital_{capital}_policy_{policy}_gas_{profile}'
    writecsv(OUT/(prefix+'_trades.csv'),trades);writecsv(OUT/(prefix+'_decisions.csv'),decisions)
    return result
if __name__=='__main__':
    print(validate_grid())
    results=[simulate(cap,p,g) for cap in (100,1000) for p in ('1','10','100','greedy_grid') for g in GAS]
    (OUT/'summaries.json').write_text(json.dumps(results,indent=2));writecsv(OUT/'summary.csv',results)
    for r in results:print(r['capital_usdc'],r['policy'],r['gas_scenario'],r['trade_count'],float(r['acquisition_reference_surplus_jpy']),float(r['hypothetical_dex_exit_vs_hold_usdc_jpy']))
