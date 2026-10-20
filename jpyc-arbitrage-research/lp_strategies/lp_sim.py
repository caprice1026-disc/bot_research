"""Offline fixed-order LP replay. No signing, network calls, or actual trades.
Uses Uniswap v4 formulas and validated no-hook replay from sibling stateful/.
LP fees use per-step Q128 fee growth; token collection floors at withdrawal.
"""
import sys,json,math,csv
from pathlib import Path
from decimal import Decimal as D,getcontext
HERE=Path(__file__).resolve().parent
for path in (HERE.parent/'stateful', HERE.parent/'backtest/stateful'):
    if (path/'v4_math.py').exists():sys.path.insert(0,str(path));break
from v4_math import Pool,Q96,FEE_DENOM,sqrt_at_tick,tick_at_sqrt,delta0,delta1,step,UnknownRange
from replay import initial,events,BASE
from run_stateful import refs,iso
getcontext().prec=65
Q128=1<<128
def writecsv(path,rows):
    if not rows:return
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

OUT=HERE/'results';OUT.mkdir(exist_ok=True)

class PositionPool(Pool):
    def setup(self):
        self.position=None;self.growth0=0;self.growth1=0
        self.swaps_with_active_position=0;self.active_fee_segments=0;self.total_segments=0
    def swap(self,amount,z=True,exact_in=True):
        if amount<0:raise ValueError('negative amount')
        rem=amount;total_in=total_out=fees=steps=0;active=False
        while rem:
            n,initialized=self.next_tick(z);target=sqrt_at_tick(n);old=self.sqrt
            old_liquidity=self.liquidity
            new,ain,out,f=step(old,target,old_liquidity,rem,self.fee,exact_in)
            rem-=ain+f if exact_in else out
            # v4 Pool.sol: protocol share is gross step input * protocolFee / 1e6.
            # LP fee growth is the remainder divided by active liquidity.
            protocol=(ain+f)*125//FEE_DENOM
            lpfee=f-protocol
            if lpfee<0:raise AssertionError('fee split')
            pos=self.position
            if pos and pos['lower']<=self.tick<pos['upper'] and old_liquidity:
                growth=lpfee*Q128//old_liquidity
                if z:self.growth0+=growth
                else:self.growth1+=growth
                active=True;self.active_fee_segments+=1
            total_in+=ain+f;total_out+=out;fees+=f;steps+=1;self.total_segments+=1
            self.sqrt=new
            if new==target:
                if initialized:
                    net=self.ticks[n]['liquidity_net'];self.liquidity+=-net if z else net
                    if self.liquidity<0:raise ValueError('negative liquidity')
                self.tick=n-1 if z else n
            elif new!=old:self.tick=tick_at_sqrt(new)
            if steps>1000:raise RuntimeError('step limit')
        if active:self.swaps_with_active_position+=1
        return dict(amount_in=total_in,amount_out=total_out,fee_raw=fees,steps=steps)
    def amounts(self,L,lower,upper,up=False):
        a,b=sqrt_at_tick(lower),sqrt_at_tick(upper);s=self.sqrt
        if s<=a:return delta0(a,b,L,up),0
        if s>=b:return 0,delta1(a,b,L,up)
        return delta0(s,b,L,up),delta1(a,s,L,up)
    def mint(self,lower,upper,L):
        if self.position:raise ValueError('one own position only')
        if L<=0:raise ValueError('liquidity must be positive')
        if not (self.min_word <= (lower//self.spacing)>>8 <= self.max_word and self.min_word <= (upper//self.spacing)>>8 <= self.max_word):raise UnknownRange('own bounds outside captured ticks')
        a0,a1=self.amounts(L,lower,upper,True)
        self.position=dict(lower=lower,upper=upper,L=L,a0=a0,a1=a1)
        self.growth0=self.growth1=0
        self.modify(lower,upper,L)
        return a0,a1
    def values(self):
        if not self.position:return 0,0,0,0
        p=self.position;a0,a1=self.amounts(p['L'],p['lower'],p['upper'])
        return a0,a1,p['L']*self.growth0//Q128,p['L']*self.growth1//Q128
    def burn(self):
        values=self.values();p=self.position
        if not p:raise ValueError('no position')
        self.modify(p['lower'],p['upper'],-p['L']);self.position=None
        self.growth0=self.growth1=0
        return values

def newpool():
    p=initial();p.__class__=PositionPool;p.setup();return p

def base_inputs():
    rs=refs();snaps=sorted(json.loads((BASE/'public_rpc/grid/quotes_raw.json').read_text()).values(),key=lambda b:b['block_number'])
    return rs,snaps,events()

def tick_price(price):return int(math.log(float(price)*1e12)/math.log(1.0001))
def aligned_bounds(lowerP,upperP):
    # Outward tick alignment makes the advertised interval at least as wide.
    lower=math.floor(tick_price(lowerP)/10)*10
    upper=math.ceil((tick_price(upperP)+1)/10)*10
    if lower>=upper:raise ValueError('empty bounds')
    return lower,upper

def human_price(s):return (D(s)/Q96)**2/D(10)**12

def maxL(p,lower,upper,budget,fx,only0=False):
    unit=10**18;a0,a1=p.amounts(unit,lower,upper,True)
    if only0 and a1:raise ValueError('not USDC only at placement')
    unitcost=D(a0)/10**6*fx+D(a1)/10**18
    if not unitcost:raise ValueError('zero cost')
    estimate=int(budget/unitcost*unit)
    left,right=0,estimate*2+1
    while left+1<right:
        L=(left+right)//2
        a0,a1=p.amounts(L,lower,upper,True);cost=D(a0)/10**6*fx+D(a1)/10**18
        if cost<=budget:left=L
        else:right=L
    L=left;a0,a1=p.amounts(L,lower,upper,True);cost=D(a0)/10**6*fx+D(a1)/10**18
    return L,a0,a1,cost

def gas(b,r,profile,kind):
    units={'base':{'mint':250000,'burn':300000},'high':{'mint':500000,'burn':600000}}[profile][kind]
    tip=30 if profile=='base' else 100
    return D(units)*(D(b['base_fee_per_gas_wei'])+D(tip)*10**9)/10**18*D(r['pol_reference_jpy'])

def passive(capital,width,profile='base',write=True):
    p=newpool();rs,snaps,ev=base_inputs();first=snaps[0];r0=rs[first['target_time']];fx0=D(r0['usdc_reference_jpy']);capitalj=D(capital)*fx0
    mid=human_price(p.sqrt);lo,hi=aligned_bounds(mid*(1-D(str(width))),mid*(1+D(str(width))))
    L,a0,a1,cost=maxL(p,lo,hi,capitalj,fx0);p.mint(lo,hi,L)
    cash=capitalj-cost;gasj=gas(first,r0,profile,'mint');ei=0;decisions=[]
    for b in snaps:
        while ei<len(ev) and ev[ei]['block']<=b['block_number']:
            e=ev[ei]
            if e['type']=='swap':p.swap(e['input_raw'],e['zero_for_one'])
            else:p.modify(e['lower'],e['upper'],e['delta'])
            ei+=1
        x,y,f0,f1=p.values();r=rs[b['target_time']];fx=D(r['usdc_reference_jpy'])
        decisions.append(dict(time_utc=iso(b['target_time']),block=b['block_number'],tick=p.tick,in_range=lo<=p.tick<hi,usdc=str(D(x)/10**6),jpyc=str(D(y)/10**18),fee_usdc=str(D(f0)/10**6),fee_jpyc=str(D(f1)/10**18),nav_one_yen_before_gas_jpy=str(cash+D(x+f0)/10**6*fx+D(y+f1)/10**18)))
    assert ei==len(ev)
    x,y,f0,f1=p.burn();end=snaps[-1];rend=rs[end['target_time']];fx=D(rend['usdc_reference_jpy']);gasj+=gas(end,rend,profile,'burn')
    nav=cash+D(x+f0)/10**6*fx+D(y+f1)/10**18-gasj
    hodl=cash+D(a0)/10**6*fx+D(a1)/10**18
    spot_jpyc=fx/human_price(p.sqrt)
    market_nav=cash+D(x+f0)/10**6*fx+D(y+f1)/10**18*spot_jpyc-gasj
    market_hodl=cash+D(a0)/10**6*fx+D(a1)/10**18*spot_jpyc
    feej=D(f0)/10**6*fx+D(f1)/10**18
    out=dict(strategy='passive_centered',capital_usdc_equiv=capital,range_width_requested=str(width),gas_profile=profile,lower_tick=lo,upper_tick=hi,initial_jpy=str(capitalj),initial_usdc=str(D(a0)/10**6),initial_jpyc=str(D(a1)/10**18),own_liquidity=str(L),fee_usdc=str(D(f0)/10**6),fee_jpyc=str(D(f1)/10**18),fees_one_yen_jpy=str(feej),gas_jpy=str(gasj),end_usdc=str(D(x)/10**6),end_jpyc=str(D(y)/10**18),ending_nav_one_yen_jpy=str(nav),net_vs_jpy_cash_one_yen=str(nav-capitalj),net_vs_same_basket_hold_one_yen=str(nav-hodl),inventory_change_vs_hold_before_fees=str(nav-hodl-feej+gasj),terminal_pool_spot_jpy_per_jpyc=str(spot_jpyc),terminal_spot_mark_nav_jpy=str(market_nav),terminal_spot_mark_vs_same_basket_hold_jpy=str(market_nav-market_hodl),active_observations=sum(x['in_range'] for x in decisions),observations=len(snaps),external_swaps_with_own_active_liquidity=p.swaps_with_active_position,actual_realized_profit=None)
    if write:writecsv(OUT/f'passive_{capital}_{width}_{profile}_observations.csv',decisions)
    return out

def one_sided(capital,discount,profile='base',delay=0,exit_fraction='1',recycle=False,write=True):
    p=newpool();rs,snaps,ev=base_inputs();first=snaps[0];fx0=D(rs[first['target_time']]['usdc_reference_jpy']);initialj=D(capital)*fx0
    # Static fair-value anchor, NOT silently updated with future FX.
    lowerP=fx0/(1-D(str(discount)));upperP=fx0/(1-D(str(discount))-D('0.001'))
    lo=math.ceil((tick_price(lowerP)+1)/10)*10
    hi=max(lo+10,math.ceil((tick_price(upperP)+1)/10)*10)
    cash=initialj;pending=[];settled=D(0);gasj=D(0);feesj=D(0);mint_count=0;burn_count=0;ei=0;history=[];last_action=-1;original_usdc=0
    partial_exits=0;full_exits=0
    def settle(t):
        nonlocal cash,settled
        for v in pending[:]:
            if v['due']<=t:cash+=v['amount'];settled+=v['amount'];pending.remove(v)
    def close(b,forced=False):
        nonlocal cash,gasj,feesj,burn_count,last_action,partial_exits,full_exits
        r=rs[b['target_time']];fx=D(r['usdc_reference_jpy']);a0,a1,f0,f1=p.burn();cash+=D(a0+f0)/10**6*fx
        gross=D(a1+f1)/10**18
        if gross:pending.append(dict(due=b['target_time']+delay,amount=gross))
        feesj+=D(f0)/10**6*fx+D(f1)/10**18;g=gas(b,r,profile,'burn');gasj+=g;burn_count+=1;last_action=b['target_time']
        if a0==0:full_exits+=1
        elif a1:partial_exits+=1
        history.append(dict(action='forced_end_burn' if forced else 'trigger_burn',time_utc=iso(b['target_time']),usdc=str(D(a0+f0)/10**6),jpyc=str(gross),cash_jpy=str(cash),gas_jpy=str(g),tick=p.tick))
        settle(b['target_time'])
    for b in snaps:
        t=b['target_time'];r=rs[t];fx=D(r['usdc_reference_jpy'])
        while ei<len(ev) and ev[ei]['block']<=b['block_number']:
            e=ev[ei]
            if e['type']=='swap':p.swap(e['input_raw'],e['zero_for_one'])
            else:p.modify(e['lower'],e['upper'],e['delta'])
            ei+=1
        settle(t)
        if p.position:
            a0,a1,f0,f1=p.values();converted=1-D(a0)/original_usdc
            if converted>=D(exit_fraction) and a1:
                close(b)
        elif t!=last_action and (mint_count==0 or recycle) and t!=snaps[-1]['target_time'] and p.sqrt<=sqrt_at_tick(lo):
            # Reuse only a finite received cash budget, including entry-rate conversion.
            budget=min(initialj,cash)
            if budget>=initialj*D('0.99'):
                L,x,y,cost=maxL(p,lo,hi,budget,fx,only0=True)
                p.mint(lo,hi,L);original_usdc=x;cash-=cost;mint_count+=1;g=gas(b,r,profile,'mint');gasj+=g;last_action=t
                history.append(dict(action='mint',time_utc=iso(t),usdc=str(D(x)/10**6),jpyc='0',cash_jpy=str(cash),gas_jpy=str(g),tick=p.tick))
        assert cash>=0
    if p.position:close(snaps[-1],True)
    settle(snaps[-1]['target_time']);pend=sum((v['amount'] for v in pending),D(0));nav=cash+pend-gasj
    out=dict(strategy='one_sided_static_discount',capital_usdc_equiv=capital,discount_requested=str(discount),gas_profile=profile,redemption_delay_hours=str(D(delay)/3600),exit_fraction=exit_fraction,recycle=recycle,lower_tick=lo,upper_tick=hi,initial_jpy=str(initialj),initial_anchor_fx=str(fx0),band_jpyc_price_at_initial_fx_low=str(fx0/human_price(sqrt_at_tick(hi))),band_jpyc_price_at_initial_fx_high=str(fx0/human_price(sqrt_at_tick(lo))),mints=mint_count,burns=burn_count,full_exits=full_exits,partial_exits=partial_exits,fees_one_yen_jpy=str(feesj),gas_jpy=str(gasj),cash_after_receipts_before_external_gas_jpy=str(cash),settled_gross_jpy=str(settled),pending_gross_jpy=str(pend),ending_nav_one_yen_jpy=str(nav),net_vs_jpy_cash_one_yen=str(nav-initialj),net_vs_usdc_hold_one_yen=str(nav-D(capital)*D(rs[snaps[-1]['target_time']]['usdc_reference_jpy'])),all_redemptions_settled=not bool(pending),external_swaps_with_own_active_liquidity=p.swaps_with_active_position,actual_realized_profit=None)
    if write:writecsv(OUT/f'one_sided_{capital}_{discount}_{profile}_{delay}_{exit_fraction}_{recycle}_actions.csv',history)
    return out

def run():
    results=[]
    for cap in (100,1000):
        for width in ('.001','.005','.01'):
            for profile in ('base','high'):results.append(passive(cap,width,profile))
        for discount in ('.005','.0075','.01'):
            for fraction in ('1','.25'):
                for delay in (0,3600,86400):results.append(one_sided(cap,discount,delay=delay,exit_fraction=fraction,recycle=True))
    (OUT/'summaries.json').write_text(json.dumps(results,indent=2));writecsv(OUT/'summary.csv',results)
    for r in results:
        print(r['strategy'],r['capital_usdc_equiv'],r.get('range_width_requested',r.get('discount_requested')),r.get('exit_fraction',''),r.get('redemption_delay_hours',''),r.get('gas_profile'), 'profit',round(D(r['net_vs_jpy_cash_one_yen']),2),'fees',round(D(r['fees_one_yen_jpy']),2),'gas',round(D(r['gas_jpy']),2),'mints',r.get('mints',''),'full',r.get('full_exits',''),'part',r.get('partial_exits',''))
    return results
if __name__=='__main__':run()
