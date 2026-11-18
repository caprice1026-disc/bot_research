"""Post hoc controls, declared separately from the primary fixed experiment."""
from pathlib import Path
import json
import sys
import hashlib
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from data_inputs import DATA,write_json,sha256_file
from engine import replay,performance
from experiments import concentration,paired_bootstrap

OUT=HERE/"results"/"supplement"


def main():
    config=json.loads((HERE/"config.json").read_text())
    settings=json.loads((Path(__file__).parent/"config.json").read_text())
    identity=hashlib.sha256((sha256_file(Path(__file__))+sha256_file(Path(__file__).parent/"config.json")+
                             sha256_file(HERE/"engine.py")+sha256_file(HERE/"experiments.py")+
                             sha256_file(HERE/"config.json")+sha256_file(DATA/"market_1m.npz")+sha256_file(DATA/"target_paths.npz")).encode()).hexdigest()
    OUT.mkdir(exist_ok=True,parents=True)
    previous=OUT/"manifest.json"
    if previous.exists():
        prior=json.loads(previous.read_text())
        if prior["identity"]==identity:
            for path,digest in prior["artifacts"].items():assert sha256_file(OUT/path)==digest
            print("complete matching supplement reused");return
    with np.load(DATA/"market_1m.npz") as raw:m={k:raw[k] for k in raw.files}
    with np.load(DATA/"target_paths.npz") as raw:targets={k:raw[k] for k in raw.files}
    execution=targets["execution"];decision=execution-config["execution_delay_minutes"]
    start=int(np.searchsorted(m["timestamp"],pd.Timestamp(config["test_start"],tz="UTC").value//10**6));end=len(m["open"])
    day=decision//1440*1440
    private=[]
    for lag,sign in [(x,1) for x in settings["daily_momentum_days"]]+[(x,-1) for x in settings["daily_reversion_days"]]:
        old=m["close"][day-1-lag*1440];known=m["close"][day-1]
        private.append(sign*np.sign(known-old))
    private=np.asarray(private)
    shared=(1-settings["common_signal_weight"])*private+settings["common_signal_weight"]*private[1]
    scale=np.minimum(1.,settings["net_target_cap"]/np.maximum(np.abs(shared.mean(axis=0)),1e-12))
    groups=dict(daily_independent=private,daily_shared75=shared,daily_shared75_cap035=shared*scale,
                daily_shared75_half=shared*settings["static_shared_scale"])
    scenarios={**groups,"daily_trend7d":[private[1]],"daily_trend7d_half":[private[1]*.5],"long_003":[np.full(len(day),.03)]}
    stats_rows=[];daily_frames=[];daily_returns={};monthly_rows=[]
    for slip in config["slippage_bps"]:
        for name,paths in scenarios.items():
            eq=np.zeros(end-start);exp=np.zeros(end-start);total=np.zeros(10)
            for target in paths:
                a,b,_,t=replay(m,execution,target,start,end,config,slip)
                eq+=a;exp+=b/len(paths);total+=t
            stats,daily,ret=performance(eq,exp,total,config["initial_equity"]*len(paths),m["timestamp"][start:end])
            stats_rows.append(dict(method=name,slippage_bps=slip,delay_minutes=1,**stats))
            daily_frames.append(pd.DataFrame(dict(method=name,slippage_bps=slip,date=daily.index.strftime("%Y-%m-%d"),equity=daily.to_numpy(),return_fraction=ret.to_numpy())))
            for month,value in ((1+ret).groupby(ret.index.strftime("%Y-%m")).prod()-1).items():
                monthly_rows.append(dict(method=name,slippage_bps=slip,month=month,return_pct=value*100))
            if slip==1.:daily_returns[name]=ret.to_numpy()
    for delay in settings["decision_delay_minutes"]:
        actual=decision+delay
        valid=actual<end
        a,b,_,t=replay(m,actual[valid],targets["rl_net_risk10"][valid],start,end,config,1.)
        stats,_,_=performance(a,b,t,config["initial_equity"],m["timestamp"][start:end])
        stats_rows.append(dict(method="rl_net_risk10_latency",slippage_bps=1.,delay_minutes=delay,**stats))
    pd.DataFrame(stats_rows).to_csv(OUT/"performance.csv",index=False)
    pd.concat(daily_frames).to_csv(OUT/"daily.csv",index=False)
    pd.DataFrame(monthly_rows).to_csv(OUT/"monthly.csv",index=False)
    pd.DataFrame([dict(method=k,**concentration(v)) for k,v in groups.items()]).to_csv(OUT/"concentration.csv",index=False)
    pairs=[("daily_shared75","daily_independent"),("daily_shared75_cap035","daily_shared75"),
           ("daily_shared75_cap035","daily_shared75_half"),("daily_trend7d","cash")]
    daily_returns["cash"]=np.zeros(len(next(iter(daily_returns.values()))))
    uncertainty=[]
    for a,b in pairs:
        uncertainty.extend(dict(method=a,baseline=b,**x) for x in paired_bootstrap(daily_returns[a],daily_returns[b],config))
    pd.DataFrame(uncertainty).to_csv(OUT/"paired_uncertainty.csv",index=False)
    artifacts={p.name:sha256_file(p) for p in OUT.glob("*.csv")}
    write_json(previous,dict(status="complete",identity=identity,settings=settings,artifacts=artifacts,
                           scope="post hoc rule controls and fixed-policy latency sensitivity, not a new untouched validation set"))
    print(pd.DataFrame(stats_rows).query('slippage_bps == 1')[['method','delay_minutes','return_pct','mdd_pct','mean_abs_exposure']].to_string(index=False))


if __name__=="__main__":main()
