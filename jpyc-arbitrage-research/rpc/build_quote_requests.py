"""Offline-only builder for read-only Uniswap v4 JSON-RPC requests. Does not contact RPC."""
import json
from pool_key import keccak,word,a,b
POOL_ID='0x4643e3152a6ea3957e087cefbba74a1c628aa643e6b8a0edfae23a5796dde012'
QUOTER='0xb3d5c3dfc3a7aebff71895a7191796bffc2c81b9'
STATEVIEW='0x5ea1bd7974c8a611cbab0bdcafcb1d9cc9b3ba5a'
KEY=[a,b,500,10,0]
assert '0x'+keccak(b''.join(word(v) for v in KEY)).hex()==POOL_ID
# Dynamic struct argument: argument offset32, five-word static key, bool,
# exactAmount, hookData offset256 relative to struct, then empty bytes length0.
def quote_request(amount_raw,block='latest'):
 assert 0<amount_raw<2**128
 sig='quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))'
 data=keccak(sig.encode())[:4]+b''.join(word(v) for v in [32,*KEY,1,amount_raw,256,0])
 return {'jsonrpc':'2.0','id':1,'method':'eth_call','params':[{'to':QUOTER,'data':'0x'+data.hex()},block]}
def state_request(method,block='latest'):
 return {'jsonrpc':'2.0','id':1,'method':'eth_call','params':[{'to':STATEVIEW,'data':'0x'+keccak((method+'(bytes32)').encode())[:4].hex()+POOL_ID[2:]},block]}
if __name__=='__main__':
 # 1 USDC if decimals6, which must be confirmed on-chain before interpreting quotes.
 print(json.dumps({'status':'prepared_not_executed','pool_id':POOL_ID,'pool_key':dict(zip(['currency0','currency1','fee','tickSpacing','hooks'],KEY)), 'requests':[state_request('getSlot0'),state_request('getLiquidity'),quote_request(1000000)]},indent=2))
