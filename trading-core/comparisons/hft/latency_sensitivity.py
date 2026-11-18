"""Synthetic maker latency sensitivity; no live market access."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import numpy as np
import hftbacktest as h

BASE=1_700_000_000_000_000_000
# At 0s bid=100x1, ask=101x1; at .3s a seller trades 2 at bid.
# Depth updates restore the unchanged resting quote; no later trade occurs.
ROWS=[(h.DEPTH_EVENT|h.BUY_EVENT,0,100.,1.),
      (h.DEPTH_EVENT|h.SELL_EVENT,0,101.,1.),
      (h.TRADE_EVENT|h.SELL_EVENT,300_000_000,100.,2.),
      (h.DEPTH_EVENT|h.BUY_EVENT,300_000_001,100.,1.),
      (h.DEPTH_EVENT|h.SELL_EVENT,3_000_000_000,101.,1.)]

def run(entry_latency):
    data=np.zeros(len(ROWS),dtype=h.event_dtype)
    for i,(ev,offset,px,qty) in enumerate(ROWS):
        data[i]['ev']=ev|h.EXCH_EVENT|h.LOCAL_EVENT
        data[i]['exch_ts']=BASE+offset; data[i]['local_ts']=BASE+offset
        data[i]['px']=px; data[i]['qty']=qty
    asset=(h.BacktestAsset().data(data).linear_asset(1.).constant_order_latency(entry_latency,0)
        .risk_adverse_queue_model().no_partial_fill_exchange().trading_value_fee_model(0.,0.)
        .tick_size(1.).lot_size(1.))
    bt=h.HashMapMarketDepthBacktest([asset])
    try:
        assert bt.elapse(100_000_000)==0
        submitted=int(bt.current_timestamp)
        assert submitted==BASE+100_000_000
        assert bt.submit_buy_order(0,1,100.,1.,h.GTX,h.LIMIT,False)==0
        assert bt.elapse(2_000_000_000)==0
        order=bt.orders(0).get(1)
        filled=order.status==h.FILLED
        return {'entry_latency_ns':entry_latency,'response_latency_ns':0,
            'submitted_ns':submitted,'observed_ns':int(bt.current_timestamp),
            'status':'FILLED' if filled else 'NEW' if order.status==h.NEW else str(order.status),
            'position':float(bt.position(0)), 'filled_qty':float(order.exec_qty),
            'fill_price':float(order.exec_price) if filled else None,
            'liquidity_role':'maker_post_only' if filled else None,
            'exchange_processed_ns':int(order.exch_timestamp)}
    finally:
        bt.close()

def compare():
    fast=run(10_000_000); slow=run(500_000_000)
    assert fast['status']=='FILLED' and fast['filled_qty']==1 and fast['liquidity_role']=='maker_post_only'
    assert slow['status']=='NEW' and slow['filled_qty']==0 and slow['position']==0
    assert fast==run(10_000_000) and slow==run(500_000_000)
    return {'status':'PASS','fixture':'synthetic_maker_entry_latency','python':platform.python_version(),
        'hftbacktest':importlib.metadata.version('hftbacktest'),
        'source_rows_sha256':hashlib.sha256(json.dumps(ROWS).encode()).hexdigest(),
        'queue_model':'risk_adverse','exchange_model':'no_partial_fill','maker_fee':0,'taker_fee':0,
        'fast':fast,'slow':slow,'not_validated':['real queue calibration','partial fills','market impact','profitability','live execution']}

if __name__=='__main__':
    report=compare()
    if '--write' in sys.argv:
        Path(__file__).with_name('result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
