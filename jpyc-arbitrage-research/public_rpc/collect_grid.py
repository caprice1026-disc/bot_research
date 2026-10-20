"""Read-only, resumable historical quotes; no private keys or transactions."""
import sys,json,csv,subprocess,pathlib,datetime,time,bisect
from decimal import Decimal
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'rpc'))
from build_quote_requests import quote_request,state_request,POOL_ID
P=pathlib.Path(__file__).resolve().parent/'grid';P.mkdir(exist_ok=True)
URL='https://tenderly.rpc.polygon.community'
cache={}
for p in pathlib.Path(__file__).resolve().parent.glob('call_*.json'):
 v=json.loads(p.read_text());r=json.loads(v['response'])
 if isinstance(r,dict) and isinstance(r.get('result'),dict) and 'number' in r['result']:
  b=r['result'];cache[int(b['number'],16)]=b
if (P/'blocks.json').exists():cache.update({int(k):v for k,v in json.loads((P/'blocks.json').read_text()).items()})
ref=list(csv.DictReader(open(pathlib.Path(__file__).resolve().parents[1]/'reference/seed_aligned_reference_prices.csv',encoding='utf-8-sig')))
refs=[r for r in ref if r['usdc_status']=='ok' and r['pol_status']=='ok']
# Saved status strings may be different; require finite explicit prior quotes.
refs=[r for r in ref if r['usdc_reference_jpy'] and r['pol_reference_jpy']]
callno=len(list(P.glob('rpc_*.json')))
def rpc(reqs):
 global callno
 callno+=1;started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 proc=subprocess.run(['curl','-sS','--max-time','60','-H','Content-Type: application/json','--data',json.dumps(reqs),URL],capture_output=True,text=True)
 (P/f'rpc_{callno:04d}.json').write_text(json.dumps({'collected_at':started,'request':reqs,'returncode':proc.returncode,'stderr':proc.stderr,'response':proc.stdout}))
 if proc.returncode:raise RuntimeError(proc.stderr)
 try:v=json.loads(proc.stdout)
 except Exception:raise RuntimeError(proc.stdout[:300])
 if not isinstance(v,list):raise RuntimeError(v)
 if any('error'in x for x in v):raise RuntimeError([x for x in v if 'error'in x])
 return {x['id']:x['result'] for x in v}
def fetchblocks(nums):
 nums=sorted(set(nums)-set(cache))
 for off in range(0,len(nums),20):
  ns=nums[off:off+20];results=rpc([{'jsonrpc':'2.0','id':n,'method':'eth_getBlockByNumber','params':[hex(n),False]} for n in ns])
  for n in ns:
   b=results[n]
   if not b or int(b['number'],16)!=n:raise RuntimeError('missing/wrong block')
   cache[n]=b
  (P/'blocks.json').write_text(json.dumps(cache))
  print('blocks',len(cache),flush=True)
def ts(n):return int(cache[n]['timestamp'],16)
targets=sorted(set(int(r['target_timestamp_ms'])//1000 for r in refs))
# Estimate with actual observed ~1.5 second recent block times; must validate actual timestamps.
anchor=95859088;anchor_t=1792494000
fetchblocks([int(anchor+(t-anchor_t)/1.5) for t in targets])
resolved={}
for roundno in range(30):
 req=[];ns=sorted(cache);tss=[ts(n) for n in ns]
 for t in targets:
  if t in resolved:continue
  ix=bisect.bisect_right(tss,t)-1
  if ix<0:req.append(ns[0]-max(1,int((tss[0]-t)/1.5)+10));continue
  lo=ns[ix]
  if ix==len(ns)-1:req.append(lo+max(1,int((t-ts(lo))/1.5)+2));continue
  hi=ns[ix+1]
  if hi==lo+1:
   assert ts(lo)<=t<ts(hi);resolved[t]=lo;continue
  guess=lo+int((t-ts(lo))*(hi-lo)/(ts(hi)-ts(lo)))
  guess=max(lo+1,min(hi-1,guess));req.extend([guess,guess+1])
 print('resolved',len(resolved),'/',len(targets),'round',roundno,flush=True)
 if len(resolved)==len(targets):break
 fetchblocks(req)
else:raise RuntimeError('block mapping nonconvergence')
(P/'target_blocks.json').write_text(json.dumps(resolved,indent=2))
sizes=[1,10,100,1000]
quotes=json.loads((P/'quotes_raw.json').read_text()) if (P/'quotes_raw.json').exists() else {}
pending=[t for t in targets if str(resolved[t]) not in quotes]
for off in range(0,len(pending),3):
 group=pending[off:off+3];requests=[]
 for j,t in enumerate(group):
  n=resolved[t]
  rs=[state_request('getSlot0',hex(n)),state_request('getLiquidity',hex(n))]+[quote_request(s*1000000,hex(n)) for s in sizes]
  for i,r in enumerate(rs):r['id']=j*6+i
  requests.extend(rs)
 v=rpc(requests)
 for j,t in enumerate(group):
  n=resolved[t];key=str(n);k=j*6
  quotes[key]={'target_time':t,'block_number':n,'block_time':ts(n),'base_fee_per_gas_wei':int(cache[n]['baseFeePerGas'],16),'slot0':v[k],'liquidity_raw':int(v[k+1],16),'sizes':{str(s):{'amount_out_raw':int(v[k+2+i][2:66],16),'quoter_gas_estimate':int(v[k+2+i][66:130],16)}for i,s in enumerate(sizes)}}
 (P/'quotes_raw.json').write_text(json.dumps(quotes,indent=2))
 print('quotes',len(quotes),'/',len(targets),flush=True)
print('DONE',len(quotes),flush=True)
