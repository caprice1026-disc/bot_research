"""Fixed protocol, chronological out-of-sample experiments and artifact checks."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

import numpy as np
import pandas as pd

from data_inputs import DATA,HERE,OUT,write_json,sha256_file
from engine import features,encode_state,period_outcomes,fit_policy,policy_targets,replay,performance
from operational_audit import faults


def input_identity(config):
    sources={p.name:sha256_file(p) for p in sorted(HERE.glob("*.py"))}
    payload=dict(config=config,sources=sources,data=sha256_file(DATA/"market_1m.npz"))
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),payload


def month_targets(market, feature, config):
    actions=np.asarray(config["actions"])
    returns,funding,next_execution=period_outcomes(market,feature["execution_index"])
    times=pd.to_datetime(feature["timestamp"],unit="ms",utc=True)
    test=times>=pd.Timestamp(config["test_start"],tz="UTC")
    targets={"cash":np.zeros(test.sum()),"long_1x":np.ones(test.sum()),"short_1x":-np.ones(test.sum())}
    z=feature["z60"][test]
    targets["trend_1h"]=np.where(z>.5,1.,np.where(z<-.5,-1.,0.))
    targets["reversion_1h"]=-targets["trend_1h"]
    base_cost=(config["fee_bps"]+config["slippage_bps"][0])/10000
    discount=config["rl_discount"]
    arms={"rl_gross":(0.,0.,discount),"myopic_net":(base_cost,0.,0.),
          "rl_net":(base_cost,0.,discount),"rl_net_risk10":(base_cost,10.,discount),"rl_net_risk50":(base_cost,50.,discount),
          "rl_cost_half":(base_cost*.5,10.,discount),"rl_cost_double":(base_cost*2,10.,discount)}
    targets.update({name:np.full(test.sum(),np.nan) for name in arms})
    fit_records=[]; policies={}; previous={name:2 for name in arms}
    for month in pd.date_range(config["test_start"],config["end_exclusive"],freq="MS",inclusive="left",tz="UTC"):
        stop=month+pd.DateOffset(months=1)
        start=month-pd.DateOffset(months=config["training_months"])
        eval_mask=(times>=month)&(times<stop)
        # Purge labels that end at or after the evaluation boundary.
        train=(times>=start)&(times<month)&(next_execution < np.searchsorted(market["timestamp"],month.value//10**6))
        threshold=float(np.median(feature["vol"][train]))
        state=encode_state(feature,threshold)
        for name,(cost,risk,discount) in arms.items():
            pol,details=fit_policy(state,returns,funding,train,actions,cost,risk,discount,config["rl_iterations"])
            sequence=policy_targets(pol,state[eval_mask],actions,previous[name])
            previous[name]=int(np.flatnonzero(actions==sequence[-1])[0])
            targets[name][eval_mask[test]]=sequence
            key=f"{month.strftime('%Y-%m')}_{name}"
            policies[key]=pol
            fit_records.append(dict(month=month.strftime("%Y-%m"),method=name,train_start=start.isoformat(),
                                    latest_label_end=pd.to_datetime(market["timestamp"][next_execution[train].max()],unit="ms",utc=True).isoformat(),
                                    test_start=month.isoformat(),test_end_exclusive=stop.isoformat(),vol_threshold=threshold,**details))
        print(f"fit complete {month.strftime('%Y-%m')}",flush=True)
    if any(not np.all(np.isfinite(x)) for x in targets.values()):
        raise ValueError("missing target path")
    np.savez_compressed(DATA/"policies.npz",**policies)
    pd.DataFrame(fit_records).to_csv(OUT/"fit_records.csv",index=False)
    return targets,test


def make_swarms(feature,test):
    private=[]
    for key,sign in (("z15",1),("z30",1),("z60",1),("z240",1),("z15",-1),("z60",-1)):
        z=feature[key][test]
        private.append(sign*np.where(z>.5,1.,np.where(z<-.5,-1.,0.)))
    independent=np.asarray(private)
    shared=.25*independent+.75*independent[2]
    scale=np.minimum(1.,.35/np.maximum(np.abs(shared.mean(axis=0)),1e-12))
    return dict(swarm_independent=independent,swarm_shared75=shared,swarm_shared75_cap035=shared*scale,
                swarm_shared75_half=shared*.5)


def concentration(targets):
    overlaps=[]; corrs=[]; simultaneous=[]
    reductions=np.diff(np.abs(targets),axis=1)<-1e-8
    for i in range(len(targets)):
        for j in range(i+1,len(targets)):
            a,b=targets[i],targets[j]
            overlaps.append(np.mean(np.minimum(np.abs(a),np.abs(b))*(a*b>0)))
            corrs.append(np.corrcoef(a,b)[0,1])
            both=reductions[i]&reductions[j]
            either=reductions[i]|reductions[j]
            simultaneous.append(both.sum()/max(1,either.sum()))
    return dict(mean_pairwise_target_correlation=float(np.mean(corrs)),mean_same_direction_overlap=float(np.mean(overlaps)),
                simultaneous_reduction_given_either=float(np.mean(simultaneous)),mean_gross_target=float(np.abs(targets).mean()),
                mean_abs_net_target=float(np.abs(targets.mean(axis=0)).mean()))


def paired_bootstrap(a,b,config):
    delta=np.asarray(a)-np.asarray(b)
    rng=np.random.default_rng(config["seed"])
    rows=[]
    for block in config["bootstrap_block_days"]:
        starts=rng.integers(len(delta),size=(config["bootstrap_samples"],int(np.ceil(len(delta)/block))))
        indices=(starts[:,:,None]+np.arange(block))%len(delta)
        means=delta[indices.reshape(len(starts),-1)[:,:len(delta)]].mean(axis=1)*10000
        ci=np.quantile(means,[.025,.975])
        rows.append(dict(block_days=block,mean_daily_difference_bps=float(delta.mean()*10000),ci95_low_bps=float(ci[0]),ci95_high_bps=float(ci[1])))
    return rows


def run(config):
    OUT.mkdir(exist_ok=True,parents=True)
    identity,inputs=input_identity(config)
    prior=OUT/"run_manifest.json"
    if prior.exists():
        saved=json.loads(prior.read_text(encoding="utf-8"))
        if saved.get("identity")==identity and saved.get("status")=="complete":
            verify(config)
            print("successful identical experiment reused",flush=True)
            return
    write_json(prior,dict(status="running",identity=identity,inputs=inputs))
    with np.load(DATA/"market_1m.npz") as raw:
        market={name:raw[name] for name in raw.files}
    feature=features(market,config["execution_delay_minutes"])
    targets,test=month_targets(market,feature,config)
    execution=feature["execution_index"][test]
    start=int(np.searchsorted(market["timestamp"],pd.Timestamp(config["test_start"],tz="UTC").value//10**6))
    end=len(market["timestamp"])
    timestamps=market["timestamp"][start:end]
    summaries=[]; daily_frames=[]; monthly_frames=[]; curve_store={}; individual_daily={}
    scenarios={name:[target] for name,target in targets.items()}
    swarm=make_swarms(feature,test)
    scenarios.update(swarm)
    concentration_rows=[dict(method=name,**concentration(paths)) for name,paths in swarm.items()]
    for slip in config["slippage_bps"]:
        for name,paths in scenarios.items():
            paths=list(paths)
            aggregate=np.zeros(end-start); exp=np.zeros(end-start); total=np.zeros(9)
            for target in paths:
                equity,exposure,qty,totals=replay(market,execution,target,start,end,config,slip_bps=slip)
                aggregate+=equity; exp+=exposure/len(paths); total+=totals
            initial=config["initial_equity"]*len(paths)
            stats,daily,returns=performance(aggregate,exp,total,initial,timestamps)
            summaries.append(dict(method=name,slippage_bps=slip,accounts=len(paths),**stats))
            daily_frames.append(pd.DataFrame(dict(date=daily.index.strftime("%Y-%m-%d"),method=name,slippage_bps=slip,equity=daily.to_numpy(),return_fraction=returns.to_numpy())))
            monthly=(1+returns).groupby(returns.index.strftime("%Y-%m")).prod()-1
            for month,value in monthly.items():monthly_frames.append(dict(method=name,slippage_bps=slip,month=month,return_pct=value*100))
            if slip==1.:
                individual_daily[name]=returns.to_numpy()
                curve_store[name]=daily.to_numpy()/initial
            if abs(aggregate[-1]-(initial+total[5]-total[1]-total[2]-total[3]))>1e-5:
                raise AssertionError("self-financing ledger does not reconcile")
        print(f"replay complete, slippage {slip} bp",flush=True)
    summary=pd.DataFrame(summaries)
    summary.to_csv(OUT/"performance.csv",index=False)
    pd.concat(daily_frames).to_csv(OUT/"daily.csv",index=False)
    pd.DataFrame(monthly_frames).to_csv(OUT/"monthly.csv",index=False)
    pd.DataFrame(concentration_rows).to_csv(OUT/"concentration.csv",index=False)
    pairs=[("rl_net","rl_gross"),("rl_net_risk10","rl_net"),("rl_net_risk50","rl_net"),("rl_net","myopic_net"),
           ("rl_net_risk10","trend_1h"),("swarm_shared75","swarm_independent"),("swarm_shared75_cap035","swarm_shared75"),
           ("swarm_shared75_cap035","swarm_shared75_half")]
    pairs += [(name,"cash") for name in targets if name not in ("cash",)]
    uncertainty=[]
    for a,b in pairs:
        uncertainty.extend(dict(method=a,baseline=b,**r) for r in paired_bootstrap(individual_daily[a],individual_daily[b],config))
    pd.DataFrame(uncertainty).to_csv(OUT/"paired_uncertainty.csv",index=False)
    fault=pd.DataFrame(faults(market,execution,config["seed"]))
    fault.to_csv(OUT/"fault_trials.csv",index=False)
    fault.groupby(["scenario","mode"]).agg(trials=("timestamp","count"),unintended_notional_usdt=("unintended_notional","sum"),
                                             missing_protection=("missing_protection","sum"),fees_usdt=("fees","sum")).reset_index().to_csv(OUT/"fault_summary.csv",index=False)
    np.savez_compressed(DATA/"target_paths.npz",execution=execution,**targets,**swarm)
    plot(curve_store,summary,daily.index)
    artifacts={str(p.relative_to(HERE)):sha256_file(p) for p in sorted(OUT.glob("*")) if p.is_file() and p.name not in ("run_manifest.json","verification.json")}
    write_json(prior,dict(status="complete",identity=identity,inputs=inputs,artifacts=artifacts,
                         environment=dict(python=sys.version,numpy=np.__version__,pandas=pd.__version__,platform=platform.platform()),
                         scope="independent paper-inspired experiments; no LLM inference; not reproduction of historical paper runs"))
    verify(config)


def plot(curves,summary,dates):
    os.environ["MPLCONFIGDIR"]=str(DATA/"matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(11,9),sharex=True)
    for name in ("cash","long_1x","trend_1h","rl_gross","rl_net","rl_net_risk10","rl_net_risk50"):
        axes[0].plot(dates,(curves[name]-1)*100,label=name,lw=1.4)
    for name in ("swarm_independent","swarm_shared75","swarm_shared75_cap035","swarm_shared75_half"):
        axes[1].plot(dates,(curves[name]-1)*100,label=name,lw=1.5)
    axes[0].set_title("BTCUSDT perpetual | Jan-Sep 2026 | out-of-sample")
    axes[1].set_title("Rule-agent concentration mechanism (no LLMs)")
    for ax in axes:
        ax.set_ylabel("Net return (%)");ax.grid(alpha=.2);ax.legend(fontsize=8,ncol=2)
    fig.suptitle("5 bp fee + 1 bp slippage per side + historical funding",fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT/"equity_curves.png",dpi=160)
    plt.close(fig)


def verify(config):
    identity,_=input_identity(config)
    manifest=json.loads((OUT/"run_manifest.json").read_text(encoding="utf-8"))
    if manifest["status"]!="complete" or identity!=manifest["identity"]:
        raise AssertionError("unfinished run or changed source/config/data")
    for name,digest in manifest["artifacts"].items():
        if sha256_file(HERE/name)!=digest:raise AssertionError(f"artifact changed: {name}")
    quality=json.loads((OUT/"data_quality.json").read_text(encoding="utf-8"))
    assert quality["status"]=="complete" and quality["funding"]["rows"]==1095
    fit=pd.read_csv(OUT/"fit_records.csv")
    assert (pd.to_datetime(fit.latest_label_end)<pd.to_datetime(fit.test_start)).all()
    stats=pd.read_csv(OUT/"performance.csv")
    assert len(stats)==48 and np.isfinite(stats.return_pct).all()
    assert (stats.loc[stats.method=="cash","return_pct"]==0).all()
    assert (stats.loc[stats.method=="cash","order_count"]==0).all()
    fault=pd.read_csv(OUT/"fault_summary.csv")
    good=fault[fault["mode"]=="receipt_reconcile"]
    assert good.missing_protection.sum()==0 and good.unintended_notional_usdt.abs().sum()<1e-8
    from PIL import Image
    with Image.open(OUT/"equity_curves.png") as im:im.verify()
    write_json(OUT/"verification.json",dict(status="passed",experiment_rows=len(stats),monthly_fits=len(fit),
               checked_artifacts=len(manifest["artifacts"]),data_status=quality["status"],future_label_boundary="passed",
               fault_trials=int(fault.trials.sum()),scope="artifact integrity and specified invariants, not proof of profitability"))
    print("artifact verification passed",flush=True)
