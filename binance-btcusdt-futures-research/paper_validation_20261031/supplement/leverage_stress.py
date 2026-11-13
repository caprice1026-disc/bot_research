"""Fixed-policy leverage extrapolation; approximate margin, no venue guarantee."""
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

OUT=HERE/"results"/"leverage_stress"


def main():
    config=json.loads((HERE/"config.json").read_text())
    identity=hashlib.sha256((sha256_file(Path(__file__))+sha256_file(HERE/"engine.py")+
                            sha256_file(DATA/"target_paths.npz")+sha256_file(DATA/"market_1m.npz")+
                            sha256_file(HERE/"config.json")).encode()).hexdigest()
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/"manifest.json").exists():
        prior=json.loads((OUT/"manifest.json").read_text())
        if prior["identity"]==identity and sha256_file(OUT/"performance.csv")==prior["csv_sha256"]:
            print("completed identical leverage stress reused");return
    with np.load(DATA/"market_1m.npz") as raw:m={k:raw[k] for k in raw.files}
    with np.load(DATA/"target_paths.npz") as raw:p={k:raw[k] for k in raw.files}
    start=int(np.searchsorted(m["timestamp"],pd.Timestamp(config["test_start"],tz="UTC").value//10**6))
    end=len(m["timestamp"]);rows=[]
    for name in ("long_1x","rl_net","rl_net_risk10"):
        for factor in (1,2,5,10,20):
            eq,exp,_,t=replay(m,p["execution"],p[name]*factor,start,end,config,1.)
            stats,_,_=performance(eq,exp,t,config["initial_equity"],m["timestamp"][start:end])
            rows.append(dict(method=name,exposure_multiplier=factor,**stats))
    frame=pd.DataFrame(rows)
    frame.to_csv(OUT/"performance.csv",index=False)
    write_json(OUT/"manifest.json",dict(status="complete",identity=identity,csv_sha256=sha256_file(OUT/"performance.csv"),
               scope="post hoc fixed-policy extrapolation outside training action bounds; constant 0.5% maintenance proxy; not Binance liquidation replication"))
    print(frame[["method","exposure_multiplier","return_pct","mdd_pct","near_liquidation_minutes","liquidation_proxy_count"]].to_string(index=False))


if __name__=="__main__":main()
