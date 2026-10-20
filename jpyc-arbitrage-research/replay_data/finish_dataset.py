import json,pathlib,sys,subprocess,datetime,collections
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'rpc'));from pool_key import keccak
P=pathlib.Path(__file__).resolve().parent;G=P.parent/'public_rpc/grid'
logs=json.load(open(P/'pool_logs.json'));blocks={int(k):v for k,v in json.load(open(G/'blocks.json')).items()}
if (P/'event_blocks.json').exists():blocks.update({int(k):v for k,v in json.load(open(P/'event_blocks.json')).items()})
nums=sorted({int(x['blockNumber'],16)for x in logs});missing=[n for n in nums if n not in blocks]
for off in range(0,len(missing),20):
 group=missing[off:off+20];req=[{'jsonrpc':'2.0','id':n,'method':'eth_getBlockByNumber','params':[hex(n),False]}for n in group]
 p=subprocess.run(['curl','-sS','--max-time','60','-H','Content-Type: application/json','--data',json.dumps(req),'https://tenderly.rpc.polygon.community'],capture_output=True,text=True)
 (P/f'event_block_rpc_{off//20:02d}.json').write_text(json.dumps({'request':req,'response':p.stdout,'returncode':p.returncode,'stderr':p.stderr}))
 if p.returncode:raise RuntimeError(p.stderr)
 v=json.loads(p.stdout)
 for r in v:
  if 'error'in r:raise RuntimeError(r)
  b=r['result'];assert b and int(b['number'],16)==r['id'];blocks[r['id']]=b
 (P/'event_blocks.json').write_text(json.dumps({n:blocks[n]for n in nums if n in blocks},indent=2))
 print('event blocks',len([n for n in nums if n in blocks]),'/',len(nums),flush=True)
SIG={
'Swap':'Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)',
'ModifyLiquidity':'ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)',
'Donate':'Donate(bytes32,address,uint256,uint256)',
'ProtocolFeeUpdated':'ProtocolFeeUpdated(bytes32,uint24)',
'Initialize':'Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)'}
topics={'0x'+keccak(v.encode()).hex():k for k,v in SIG.items()};decoded=[];keys=set()
for x in sorted(logs,key=lambda x:(int(x['blockNumber'],16),int(x['transactionIndex'],16),int(x['logIndex'],16))):
 n=int(x['blockNumber'],16);assert 95802902<n<=95859621 and not x['removed'];assert x['blockHash']==blocks[n]['hash'];assert x['topics'][1]=='0x4643e3152a6ea3957e087cefbba74a1c628aa643e6b8a0edfae23a5796dde012'
 key=(x['blockHash'],x['logIndex']);assert key not in keys;keys.add(key)
 kind=topics.get(x['topics'][0],'unknown');s=x['data'][2:];words=[int(s[i:i+64],16)for i in range(0,len(s),64)]
 signed=lambda z:z-(1<<256)if z>>255 else z
 d={'event':kind,'block_number':n,'block_timestamp':int(blocks[n]['timestamp'],16),'transaction_index':int(x['transactionIndex'],16),'log_index':int(x['logIndex'],16),'transaction_hash':x['transactionHash']}
 if kind=='Swap':
  assert len(words)==6;d.update(dict(zip(['amount0','amount1','sqrt_price_x96','liquidity','tick','swap_fee_pips'],[signed(words[0]),signed(words[1]),words[2],words[3],signed(words[4]),words[5]])))
 elif kind=='ModifyLiquidity':
  assert len(words)==4;d.update({'tick_lower':signed(words[0]),'tick_upper':signed(words[1]),'liquidity_delta':signed(words[2]),'salt':hex(words[3]),'sender':x['topics'][2]})
 elif kind=='Donate':d.update({'amount0':words[0],'amount1':words[1]})
 elif kind=='ProtocolFeeUpdated':d['protocol_fee_packed']=words[0]
 else:raise RuntimeError('unrecognized pool log '+kind)
 decoded.append(d)
(P/'decoded_events.json').write_text(json.dumps(decoded,indent=2))
initial=json.load(open(P/'initial_ticks.json'));assert len(initial)==108
for t,v in initial.items():assert 322560<=int(t)<=330230 and int(t)%10==0 and v['liquidity_gross']>0 and abs(v['liquidity_net'])<=v['liquidity_gross']
summary={'event_counts':dict(collections.Counter(x['event']for x in decoded)),'total_events':len(decoded),'unique_event_blocks':len(nums),'start_state_block':95802902,'first_scanned_block':95802903,'last_scanned_block':95859621,'initial_bitmap_words':[126,127,128],'initial_ticks':108,'known_tick_bounds_inclusive':[322560,330230],'tick_coverage':'bounded_not_full_global_tick_space','no_duplicates':True,'block_hashes_matched':True,'unknown_events':0,'source':'https://tenderly.rpc.polygon.community'}
(P/'validation.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
