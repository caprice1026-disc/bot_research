"""One-yen redemption and finite-JPY cash recycling sensitivity. Offline only."""
import json
from decimal import Decimal as D,getcontext
from datetime import datetime,timezone
from pathlib import Path
from replay import BASE,initial,events,apply,writecsv
from run_stateful import refs,gas_cost,iso,GAS
getcontext().prec=65
OUT=BASE/'stateful/redemption_results';OUT.mkdir(exist_ok=True)
GRID=(1,10,100,1000)

def simulate_redemption(capital_usdc_equiv,policy,profile,delay_seconds=0,reinvest=True,write=True):
    if not D(str(capital_usdc_equiv)).is_finite() or D(str(capital_usdc_equiv))<=0: raise ValueError("positive finite capital required")
    if not isinstance(delay_seconds,int) or delay_seconds<0: raise ValueError("nonnegative integer delay required")
    if policy not in ("1","10","100","1000","greedy_grid") or profile not in GAS: raise ValueError("unknown policy or gas scenario")
    p=initial();ev=events();ei=0;rs=refs()
    snaps=sorted(json.loads((BASE/'public_rpc/grid/quotes_raw.json').read_text()).values(),key=lambda b:b['block_number'])
    assert [b["target_time"] for b in snaps]==sorted(set(b["target_time"] for b in snaps))
    for b in snaps:
        assert b["block_time"]<=b["target_time"]
        for token in ("usdc","pol"):
            assert int(rs[b["target_time"]][token+"_source_timestamp_ms"])<=b["target_time"]*1000
    initial_jpy=D(capital_usdc_equiv)*D(rs[snaps[0]['target_time']]['usdc_reference_jpy'])
    cash=initial_jpy;unspent_original=initial_jpy;gas_total=D(0);spent=D(0);receipts=D(0)
    lots=[];pending=[];settlements=[];decisions=[]
    def settle(now):
        nonlocal cash,receipts
        while pending and pending[0]['due_time']<=now:
            lot=pending.pop(0);lot['settled']=True;lot['settlement_time']=lot['due_time']
            receipt=lot['receipt_jpy'];cash+=receipt;receipts+=receipt
            settlements.append(dict(lot_id=lot['lot_id'],settlement_time_utc=iso(lot['due_time']),receipt_jpy=str(receipt),cash_after_jpy=str(cash),lot_cost_jpy=str(lot['cost_jpy']),lot_gas_jpy=str(lot['gas_jpy']),settled_lot_net_profit_jpy=str(receipt-lot['cost_jpy']-lot['gas_jpy'])))
    for b in snaps:
        t=b['target_time']
        while ei<len(ev) and ev[ei]['block']<=b['block_number']:apply(p,ev[ei]);ei+=1
        settle(t) # Due receipts are available before this observation's single order.
        r=rs[t];fx=D(r['usdc_reference_jpy']);cash_before=cash
        sizes=GRID if policy=='greedy_grid' else (int(policy),)
        investable=cash if reinvest else min(cash,unspent_original)
        candidates=[]
        for size in sizes:
            cost=D(size)*fx
            if cost>investable:continue
            cp=p.clone();q=cp.swap(size*10**6)
            qgas=b['sizes'][str(size)]['quoter_gas_estimate']
            gas=gas_cost(b,r,profile,qgas,q['steps']);receipt=D(q['amount_out'])/10**18
            net=receipt-cost-gas
            if net>0:candidates.append((net,size,cost,cp,q,gas,receipt))
        action='skip_nonpositive_or_cash_limit';selected_size=0
        if candidates:
            net,size,cost,cp,q,gas,receipt=max(candidates,key=lambda c:(c[0],-c[1]))
            sqrt_before=p.sqrt;p=cp;cash-=cost;spent+=cost;gas_total+=gas
            if not reinvest:unspent_original-=cost
            lot=dict(lot_id=len(lots)+1,entry_time=t,due_time=t+delay_seconds,block=b['block_number'],size_usdc=size,cost_jpy=cost,receipt_jpy=receipt,gas_jpy=gas,fx=fx,settled=False,settlement_time=None,sqrt_before=sqrt_before,sqrt_after=p.sqrt,steps=q['steps'])
            lots.append(lot);pending.append(lot);action='paper_buy';selected_size=size
            settle(t) # Immediate redemption does not permit a second order at this time.
        assert cash>=0
        decisions.append(dict(time_utc=iso(t),block=b['block_number'],action=action,selected_size_usdc=selected_size,cash_before_jpy=str(cash_before),cash_after_jpy=str(cash),investable_before_jpy=str(investable),pending_gross_jpy=str(sum((l['receipt_jpy'] for l in pending),D(0))),counterfactual_sqrt=str(p.sqrt),counterfactual_tick=p.tick))
    assert ei==len(ev)
    # Do not move clock beyond observed window to recognize delayed receipts.
    end=snaps[-1]['target_time'];settle(end)
    pending_gross=sum((l['receipt_jpy'] for l in pending),D(0));pending_cost=sum((l['cost_jpy'] for l in pending),D(0));pending_gas=sum((l['gas_jpy'] for l in pending),D(0))
    settled_net=sum((l['receipt_jpy']-l['cost_jpy']-l['gas_jpy'] for l in lots if l['settled']),D(0))
    pending_net=pending_gross-pending_cost-pending_gas
    net_nav=cash+pending_gross-gas_total;profit=net_nav-initial_jpy
    assert abs(cash-(initial_jpy+receipts-spent))<D('1e-40')
    assert abs(profit-(settled_net+pending_net))<D('1e-40')
    assert len(lots)==len({l['entry_time'] for l in lots})<=len(snaps)
    result=dict(capital_usdc_equiv=capital_usdc_equiv,initial_swap_capital_jpy=str(initial_jpy),policy=policy,gas_scenario=profile,reinvest=reinvest,redemption_delay_hours=str(D(delay_seconds)/3600),window_start_utc=iso(snaps[0]['target_time']),window_end_utc=iso(end),observations=len(snaps),trade_count=len(lots),settled_lot_count=len(lots)-len(pending),pending_lot_count=len(pending),total_swap_usdc=str(sum((D(l['size_usdc']) for l in lots),D(0))),total_acquisition_cost_jpy=str(spent),settled_receipts_jpy=str(receipts),ending_cash_before_external_gas_jpy=str(cash),pending_redemption_gross_jpy=str(pending_gross),pending_cost_basis_jpy=str(pending_cost),total_external_gas_cost_jpy=str(gas_total),settled_lot_net_profit_jpy=str(settled_net),pending_lot_reference_net_profit_jpy=str(pending_net),net_nav_after_gas_jpy=str(net_nav),net_profit_assuming_all_one_yen_redemptions_jpy=str(profit),net_return_on_swap_capital_pct=str(profit/initial_jpy*100),all_redemptions_settled_within_window=not bool(pending),gas_funded_separately=True,actual_transactions=False,actual_realized_profit=None,unspent_original_budget_jpy=str(unspent_original) if not reinvest else None,first_trade_utc=iso(lots[0]['entry_time']) if lots else None,last_trade_utc=iso(lots[-1]['entry_time']) if lots else None)
    if write:
        mode=f'reinvest_delay_{delay_seconds//3600}h' if reinvest else 'no_reinvest'
        prefix=f'capital_{capital_usdc_equiv}_policy_{policy}_{mode}_gas_{profile}'
        serialized=[]
        for l in lots:
            serialized.append(dict(lot_id=l['lot_id'],entry_time_utc=iso(l['entry_time']),redemption_due_utc=iso(l['due_time']),block=l['block'],size_usdc=l['size_usdc'],acquisition_cost_jpy=str(l['cost_jpy']),receipt_or_receivable_jpy=str(l['receipt_jpy']),gas_jpy=str(l['gas_jpy']),reference_net_profit_jpy=str(l['receipt_jpy']-l['cost_jpy']-l['gas_jpy']),fx_jpy_per_usdc=str(l['fx']),settled_within_window=l['settled'],settlement_time_utc=iso(l['settlement_time']) if l['settled'] else '',sqrt_before=str(l['sqrt_before']),sqrt_after=str(l['sqrt_after']),steps=l['steps']))
        writecsv(OUT/(prefix+'_trades.csv'),serialized);writecsv(OUT/(prefix+'_settlements.csv'),settlements);writecsv(OUT/(prefix+'_decisions.csv'),decisions)
    return result

def run():
    result=[]
    for capital in (100,1000):
        for policy in ('1','10','100','1000','greedy_grid'):
            for profile in GAS:
                result.append(simulate_redemption(capital,policy,profile,0,False))
                for delay in (0,3600,86400):result.append(simulate_redemption(capital,policy,profile,delay,True))
    (OUT/'summaries.json').write_text(json.dumps(result,indent=2));writecsv(OUT/'summary.csv',result)
    for r in result:
        if r['capital_usdc_equiv']==1000 and r['gas_scenario']=='base':
            print(r['policy'],'reinvest',r['reinvest'],'delay',r['redemption_delay_hours'],'trades',r['trade_count'],'settled',r['settled_lot_count'],'profit',round(D(r['net_profit_assuming_all_one_yen_redemptions_jpy']),2),'realizedlots',round(D(r['settled_lot_net_profit_jpy']),2),'pending',round(D(r['pending_redemption_gross_jpy']),2))
    return result
if __name__=='__main__':run()
