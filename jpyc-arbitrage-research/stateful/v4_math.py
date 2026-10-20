"""Bounded exact-in/out concentrated-liquidity math.
Port of Uniswap v4-core MIT TickMath/SqrtPriceMath/SwapMath/Pool formulas.
Only no-hook pools, fixed fee, known bitmap range. Pure offline, no execution.
"""
from dataclasses import dataclass, field
from copy import deepcopy
from functools import lru_cache
Q96=1<<96
MAXU256=(1<<256)-1
FEE_DENOM=1000000
CONSTANTS=[int(x,16) for x in '''fffcb933bd6fad37aa2d162d1a594001 fff97272373d413259a46990580e213a fff2e50f5f656932ef12357cf3c7fdcc ffe5caca7e10e4e61c3624eaa0941cd0 ffcb9843d60f6159c9db58835c926644 ff973b41fa98c081472e6896dfb254c0 ff2ea16466c96a3843ec78b326b52861 fe5dee046a99a2a811c461f1969c3053 fcbe86c7900a88aedcffc83b479aa3a4 f987a7253ac413176f2b074cf7815e54 f3392b0822b70005940c7a398e4b70f3 e7159475a2c29b7443b29c7fa6e889d9 d097f3bdfd2022b8845ad8f792aa5825 a9f746462d870fdf8a65dc1f90e061e5 70d869a156d2a1b890bb3df62baf32f7 31be135f97d08fd981231505542fcfa6 9aa508b5b7a84e1c677de54f3e99bc9 5d6af8dedb81196699c329225ee604 2216e584f5fa1ea926041bedfe98 48a170391f7dc42444e8fa2'''.split()]
def ceildiv(a,b):
    if a<0 or b<=0: raise ValueError('nonnegative division required')
    return (a+b-1)//b
@lru_cache(maxsize=4096)
def sqrt_at_tick(tick):
    if not -887272<=tick<=887272: raise ValueError('tick range')
    a=abs(tick); p=1<<128
    for i,c in enumerate(CONSTANTS):
        if a&(1<<i): p=p*c>>128
    if tick>0:p=MAXU256//p
    return ceildiv(p,1<<32)
def tick_at_sqrt(s):
    if not sqrt_at_tick(-887272)<=s<sqrt_at_tick(887272):raise ValueError('sqrt range')
    lo,hi=-887272,887272
    while hi-lo>1:
        mid=(lo+hi)//2
        if sqrt_at_tick(mid)<=s:lo=mid
        else:hi=mid
    return lo
def delta0(a,b,L,up):
    a,b=sorted((a,b));n=L<<96
    if up:return ceildiv(ceildiv(n*(b-a),b),a)
    return n*(b-a)//b//a
def delta1(a,b,L,up):
    n=L*abs(b-a)
    return ceildiv(n,Q96) if up else n//Q96
def next0(s,L,amount,add):
    if not amount:return s
    n=L<<96;p=amount*s
    if add:
        if p<=MAXU256 and n+p<=MAXU256:return ceildiv(n*s,n+p)
        return ceildiv(n,n//s+amount)
    if p>MAXU256 or n<=p:raise ValueError('price overflow')
    return ceildiv(n*s,n-p)
def next1(s,L,amount,add):
    return s+amount*Q96//L if add else s-ceildiv(amount*Q96,L)
def step(s,target,L,remaining,fee=625,exact_in=True):
    z=s>=target
    if exact_in:
        less=remaining*(FEE_DENOM-fee)//FEE_DENOM
        amt_in=delta0(target,s,L,True) if z else delta1(s,target,L,True)
        if less>=amt_in:
            new=target;f=ceildiv(amt_in*fee,FEE_DENOM-fee)
        else:
            amt_in=less;new=next0(s,L,less,True) if z else next1(s,L,less,True);f=remaining-amt_in
        out=delta1(new,s,L,False) if z else delta0(s,new,L,False)
    else:
        out=delta1(target,s,L,False) if z else delta0(s,target,L,False)
        if remaining>=out:new=target
        else:
            out=remaining;new=next1(s,L,out,False) if z else next0(s,L,out,False)
        amt_in=delta0(new,s,L,True) if z else delta1(s,new,L,True)
        f=ceildiv(amt_in*fee,FEE_DENOM-fee)
    return new,amt_in,out,f
class UnknownRange(Exception):pass
@dataclass
class Pool:
    sqrt:int
    tick:int
    liquidity:int
    ticks:dict=field(default_factory=dict)
    min_word:int=126
    max_word:int=128
    spacing:int=10
    fee:int=625
    def clone(self):return deepcopy(self)
    def next_tick(self,z):
        c=self.tick//self.spacing
        if z:
            word=c>>8;lower=(word<<8)*self.spacing
            candidates=[t for t,v in self.ticks.items() if v['liquidity_gross'] and lower<=t<=c*self.spacing]
            n=max(candidates) if candidates else lower
        else:
            c+=1;word=c>>8;upper=((word<<8)+255)*self.spacing
            candidates=[t for t,v in self.ticks.items() if v['liquidity_gross'] and c*self.spacing<=t<=upper]
            n=min(candidates) if candidates else upper
        if not self.min_word<=word<=self.max_word:raise UnknownRange(f'unknown bitmap word {word}')
        return n,bool(candidates)
    def swap(self,amount,z=True,exact_in=True):
        if amount<0:raise ValueError('amount must be unsigned')
        rem=amount;total_in=total_out=fees=steps=0
        while rem:
            n,initialized=self.next_tick(z);target=sqrt_at_tick(n);old=self.sqrt
            new,ain,out,f=step(old,target,self.liquidity,rem,self.fee,exact_in)
            rem-=ain+f if exact_in else out
            total_in+=ain+f;total_out+=out;fees+=f;steps+=1
            self.sqrt=new
            if new==target:
                if initialized:
                    net=self.ticks[n]['liquidity_net'];self.liquidity+=-net if z else net
                    if self.liquidity<0:raise ValueError('negative liquidity')
                self.tick=n-1 if z else n
            elif new!=old:self.tick=tick_at_sqrt(new)
            if steps>1000:raise RuntimeError('step limit')
        return dict(amount_in=total_in,amount_out=total_out,fee_raw=fees,steps=steps)
    def modify(self,lower,upper,delta):
        if lower>=upper or lower%self.spacing or upper%self.spacing:raise ValueError('invalid bounds')
        for t,net in ((lower,delta),(upper,-delta)):
            if self.min_word<=((t//self.spacing)>>8)<=self.max_word:
                v=self.ticks.setdefault(t,dict(liquidity_gross=0,liquidity_net=0))
                v['liquidity_gross']+=delta;v['liquidity_net']+=net
                if v['liquidity_gross']<0:raise ValueError('negative tick gross')
        if lower<=self.tick<upper:self.liquidity+=delta
        if self.liquidity<0:raise ValueError('negative active liquidity')
def decode_slot(raw):
    a=[int(raw[i:i+64],16) for i in range(2,len(raw),64)]
    if a[1]>=(1<<255):a[1]-=1<<256
    return a
