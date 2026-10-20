import pathlib,json,sys,subprocess,datetime,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'rpc'))
from build_quote_requests import POOL_ID,STATEVIEW,keccak,word
P=pathlib.Path(__file__).resolve().parent;URL='https://tenderly.rpc.polygon.community';PM='0x67366782805870060151383f4bbff9dab53e5cd6'
start=95802902;end=95859621
counter=len(list(P.glob('replay_rpc_*.json')))
def rpc(req):
 global counter
 for attempt in range(2):
  counter+=1
  p=subprocess.run(['curl','-sS','--max-time','90','-w','\n%{http_code}','-H','Content-Type: application/json','--data',json.dumps(req),URL],capture_output=True,text=True)
  body,sep,code=p.stdout.rpartition('\n')
  (P/f'replay_rpc_{counter:03d}.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'request':req,'response':body,'http_code':code,'returncode':p.returncode,'stderr':p.stderr}))
  if code in ('401','403'):raise RuntimeError('access denied '+code)
  if p.returncode and attempt==0 and p.returncode==28:time.sleep(2);continue
  if p.returncode:raise RuntimeError(p.stderr)
  if code!='200':raise RuntimeError((code,body[:500]))
  v=json.loads(body)
  if isinstance(v,list):
   if any('error'in x for x in v):raise RuntimeError(v)
   return {x['id']:x['result'] for x in v}
  if 'error'in v:raise RuntimeError(v)
  return v['result']
 raise RuntimeError('retry exhausted')
# Three neighboring bitmap words are a bounded region, not all possible ticks.
probe=json.load(open(P/'probe_results.json'));ticks=[]
for r in probe:
 if r['id']==0:continue
 w=125+r['id'];bitmap=int(r['result'],16)
 ticks.extend(((w<<8)+i)*10 for i in range(256) if bitmap>>i&1)
initial=json.load(open(P/'initial_ticks.json')) if (P/'initial_ticks.json').exists() else {}
for offset in range(0,len(ticks),20):
 group=[t for t in ticks[offset:offset+20] if str(t)not in initial]
 if not group:continue
 req=[]
 for t in group:
  data='0x'+keccak(b'getTickLiquidity(bytes32,int24)')[:4].hex()+POOL_ID[2:]+word(t%(1<<256)).hex()
  req.append({'jsonrpc':'2.0','id':t,'method':'eth_call','params':[{'to':STATEVIEW,'data':data},hex(start)]})
 v=rpc(req)
 for t in group:
  s=v[t][2:];gross=int(s[:64],16);net=int(s[64:128],16);net=net-(1<<256) if net>>255 else net
  initial[str(t)]={'liquidity_gross':gross,'liquidity_net':net}
 (P/'initial_ticks.json').write_text(json.dumps(initial,indent=2));print('ticks',len(initial),flush=True)
# Modest 5000-block window first. 12 requests total, sequential, low load.
logs=[]
for a in range(start+1,end+1,5000):
 b=min(a+4999,end);file=P/f'logs_{a}_{b}.json'
 if file.exists():v=json.loads(file.read_text())
 else:
  req={'jsonrpc':'2.0','id':1,'method':'eth_getLogs','params':[{'address':PM,'fromBlock':hex(a),'toBlock':hex(b),'topics':[None,POOL_ID]}]}
  v=rpc(req);file.write_text(json.dumps(v,indent=2))
 logs+=v
 print('logs chunk',a,b,'count',len(v),'total',len(logs),flush=True)
(P/'pool_logs.json').write_text(json.dumps(logs,indent=2))
print('DONE',len(logs),flush=True)
