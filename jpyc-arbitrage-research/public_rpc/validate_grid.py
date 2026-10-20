import json,csv,pathlib,datetime
P=pathlib.Path(__file__).resolve().parent/'grid'
q=json.loads((P/'quotes_raw.json').read_text());blocks={int(k):v for k,v in json.loads((P/'blocks.json').read_text()).items()}
refs=list(csv.DictReader(open(pathlib.Path(__file__).resolve().parents[1]/'reference/seed_aligned_reference_prices.csv',encoding='utf-8-sig')))
refs={int(r['target_timestamp_ms'])//1000:r for r in refs if r['usdc_reference_jpy'] and r['pol_reference_jpy']}
assert len(q)==285 and len(refs)==285
assert set(x['target_time'] for x in q.values())==set(refs)
fees=set();rows=[]
for k,v in q.items():
 n=int(k);assert n==v['block_number']
 t=v['target_time'];assert int(blocks[n]['timestamp'],16)==v['block_time']<=t<int(blocks[n+1]['timestamp'],16)
 assert v['base_fee_per_gas_wei']==int(blocks[n]['baseFeePerGas'],16)>0
 assert v['liquidity_raw']>0
 assert set(v['sizes'])=={'1','10','100','1000'}
 slot=v['slot0'][2:];assert len(slot)==256
 protocol=int(slot[128:192],16);lp=int(slot[192:256],16);fees.add((protocol,lp))
 prev=0
 for s in [1,10,100,1000]:
  z=v['sizes'][str(s)];assert z['amount_out_raw']>prev and z['quoter_gas_estimate']>0
  prev=z['amount_out_raw']
  rows.append({'target_time_utc':datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat(),'block_number':n,'block_time_utc':datetime.datetime.fromtimestamp(v['block_time'],datetime.timezone.utc).isoformat(),'amount_in_usdc':s,'amount_out_jpyc_raw':str(z['amount_out_raw']),'quoter_internal_gas_estimate':z['quoter_gas_estimate'],'base_fee_per_gas_wei':v['base_fee_per_gas_wei'],'source':'https://tenderly.rpc.polygon.community','source_kind':'historical_pool_quote'})
with (P/'historical_quotes_raw.csv').open('w',encoding='utf-8-sig',newline='')as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
summary={'status':'validated','snapshots':len(q),'quote_rows':len(rows),'target_start_utc':rows[0]['target_time_utc'],'target_end_utc':rows[-1]['target_time_utc'],'unique_fees_protocol_packed_lp':sorted(fees),'latest_quote_not_present':True,'all_block_times_at_or_before_decision_and_next_block_after':True,'amount_out_includes_price_impact_and_all_swap_fees':True,'quoter_gas_is_not_full_router_gas':True,'no_real_transactions':True}
(P/'validation.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
