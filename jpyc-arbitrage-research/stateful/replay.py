"""Parse pool logs and verify no-own-trade replay before counterfactual use."""
import json,csv
from pathlib import Path
from decimal import Decimal as D
from v4_math import Pool,decode_slot
BASE=Path(__file__).resolve().parent.parent
SWAP='0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f'
MODIFY='0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec'
def signed(x):return x-(1<<256) if x>=1<<255 else x
def decode(log):
    v=[int(log['data'][i:i+64],16) for i in range(2,len(log['data']),64)]
    base=dict(block=int(log['blockNumber'],16),log_index=int(log['logIndex'],16),tx=log['transactionHash'],timestamp=int(log['blockTimestamp'],16))
    topic=log['topics'][0]
    if topic==SWAP:
        a,b=signed(v[0]),signed(v[1]);assert (a<0<b) or (b<0<a)
        return dict(**base,type='swap',amount0=a,amount1=b,sqrt=v[2],liquidity=v[3],tick=signed(v[4]),fee=v[5],zero_for_one=a<0,input_raw=-min(a,b),output_raw=max(a,b))
    if topic==MODIFY:return dict(**base,type='modify',lower=signed(v[0]),upper=signed(v[1]),delta=signed(v[2]))
    raise ValueError('Unknown event '+topic)
def initial():
    raw=json.loads((BASE/'public_rpc/grid/quotes_raw.json').read_text());q=raw['95802902'];s,t,_,_=decode_slot(q['slot0'])
    ticks={int(k):v for k,v in json.loads((BASE/'replay_data/initial_ticks.json').read_text()).items()}
    return Pool(s,t,q['liquidity_raw'],ticks)
def events(partial=False):
    path=BASE/'replay_data/pool_logs.json'
    if path.exists():logs=json.loads(path.read_text())
    elif partial:
        logs=[]
        for p in sorted((BASE/'replay_data').glob('logs_*.json')):logs+=json.loads(p.read_text())
    else:raise FileNotFoundError('All logs not yet complete')
    logs=sorted(logs,key=lambda l:(int(l['blockNumber'],16),int(l['logIndex'],16)))
    ids=[(l['blockHash'],l['logIndex']) for l in logs];assert len(set(ids))==len(ids)
    assert all(int(l['blockNumber'],16)>95802902 and not l.get('removed',False) for l in logs)
    return [decode(x) for x in logs]
def apply(pool,e,exact_in=True):
    if e['type']=='modify':pool.modify(e['lower'],e['upper'],e['delta']);return None
    assert e['fee']==625
    return pool.swap(e['input_raw'] if exact_in else e['output_raw'],e['zero_for_one'],exact_in)
def writecsv(path,rows):
    if not rows:return
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def baseline(partial=False):
    p=initial();rows=[]
    for e in events(partial):
        r=apply(p,e)
        if e['type']=='swap':
            rows.append(dict(block=e['block'],log_index=e['log_index'],timestamp=e['timestamp'],zero_for_one=e['zero_for_one'],sqrt_error=p.sqrt-e['sqrt'],price_error_bps=str(((D(p.sqrt)/D(e['sqrt']))**2-1)*10000),tick_error=p.tick-e['tick'],liquidity_error=p.liquidity-e['liquidity'],output_raw_error=r['amount_out']-e['output_raw']))
    writecsv(BASE/'stateful/baseline_comparison.csv',rows)
    print('events',len(events(partial)),'swaps',len(rows))
    print('max price error bps',max(abs(D(r['price_error_bps'])) for r in rows))
    print('bad liquidity',sum(r['liquidity_error']!=0 for r in rows),'bad ticks',sum(r['tick_error']!=0 for r in rows))
    print(rows[:4]);return p,rows
if __name__=='__main__':baseline(partial=True)
