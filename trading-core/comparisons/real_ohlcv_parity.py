"""Real OHLCV open-price proxy comparison, NOT a real quote/L2 backtest."""
from datetime import datetime, timezone
from decimal import Decimal as D
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import numpy as np
from nautilus_parity import core_run, nautilus_run

# Fixed before inspecting any prices/results in this window. End is exclusive.
START = "2025-10-01T01:00:00+00:00"
END = "2025-10-01T02:00:00+00:00"
TOLERANCE = D("0.0000001")  # USDT currency rounding, not relaxed fill prices.

def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()

def make_tape(path):
    start=int(datetime.fromisoformat(START).timestamp()*1000)
    end=int(datetime.fromisoformat(END).timestamp()*1000)
    with np.load(path,allow_pickle=False) as source:
        timestamps=source['timestamp']
        indices=np.flatnonzero((timestamps>=start)&(timestamps<end))
        assert len(indices)==60 and int(timestamps[indices[0]])==start
        assert np.all(np.diff(timestamps[indices])==60000)
        assert indices[0]>0 and timestamps[indices[0]-1]==start-60000
        assert not np.any(source['funding_event'][indices]), 'Funding-free comparison window required'
        tape=[]; decisions=[]; current=D(0); window=[]
        for offset,index in enumerate(indices):
            ts=int(timestamps[index]); prev=int(index)-1
            op=D(str(float(source['open'][index]))).quantize(D('0.01'))
            po=D(str(float(source['open'][prev]))); pc=D(str(float(source['close'][prev])))
            assert op>0 and po>0 and pc>0
            # At each five-minute boundary use ONLY the preceding completed bar.
            # Reverse through flat, never a direct reversal unsupported by core.
            target=current
            if offset%5==0:
                desired=D('0.010') if pc>po else D('-0.010') if pc<po else D(0)
                target=D(0) if current*desired<0 else desired
            if offset==59: target=D(0)  # liquidation schedule fixed in advance, not margin liquidation
            tape.append((ts,format(op,'.2f'),format(target,'.3f')))
            decisions.append({'execution_ms':ts,'signal_bar_open_ms':int(timestamps[prev]),
                'signal_available_ms':ts,'previous_open':str(po),'previous_close':str(pc),'target':str(target)})
            window.append([ts,*[float(source[k][index]) for k in ('open','high','low','close','volume')]])
            current=target
    return tape,decisions,window

def compare(path):
    tape,decisions,window=make_tape(path)
    a=core_run(tape); b=nautilus_run(tape)
    assert len(a['fills'])==len(b['fills']) and len(a['fills'])>=4
    max_fee_delta=D(0)
    for x,y in zip(a['fills'],b['fills']):
        assert x['timestamp_ms']==y['timestamp_ms']
        for field in ('signed_quantity','price'): assert D(x[field])==D(y[field])
        delta=abs(D(x['fee'])-D(y['fee'])); assert delta<=TOLERANCE
        max_fee_delta=max(max_fee_delta,delta)
    deltas={field:str(D(a[field])-D(b[field])) for field in ('cash','fees','gross_realized_pnl','quantity')}
    assert all(abs(D(value))<=TOLERANCE for value in deltas.values())
    assert D(a['quantity'])==D(b['quantity'])==0
    assert a==core_run(tape) and b==nautilus_run(tape)
    return {'status':'PASS','source_filename':path.name,'source_sha256':sha256(path),
       'window_start_utc':START,'window_end_exclusive_utc':END,'bars':60,
       'window_ohlcv_sha256':hashlib.sha256(json.dumps(window).encode()).hexdigest(),
       'execution_tape_sha256':hashlib.sha256(json.dumps(tape).encode()).hexdigest(),
       'strategy':'previous_bar_direction_every_5m_size_0.010_flat_before_reverse_final_scheduled_close',
       'python':platform.python_version(),'nautilus_trader':importlib.metadata.version('nautilus_trader'),
       'input_type':'real_1m_ohlcv_open_as_zero_spread_L1_proxy_NOT_recorded_quotes',
       'fee_rate':'0.001','cash_fee_absolute_tolerance_usdt':str(TOLERANCE),
       'fill_count':len(a['fills']),'max_fill_fee_delta':str(max_fee_delta),'core_minus_nautilus':deltas,
       'core':a,'nautilus':b,'decision_audit':decisions,
       'limitations':['No measured quote spread or depth','Zero feed and order latency',
          'Prior-bar signal assumed available exactly at next open: optimistic boundary',
          'No funding events in selected window','No liquidation or maker queue comparison',
          'Fee is fixed experimental 0.1%, not asserted venue tier','No profitability or robustness conclusion']}

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--source',type=Path,required=True); parser.add_argument('--write',action='store_true')
    args=parser.parse_args(); report=compare(args.source)
    if args.write: Path(__file__).with_name('real_ohlcv_parity_result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('core','nautilus','decision_audit')},indent=2))
