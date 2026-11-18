"""Replay archived targets against the current engine; never train/fetch/write source.

Usage from repository root:
python -B trading-core/validation/replay_archived_study.py --source-root ../bot_research
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
import numpy as np
import pandas as pd
import numba

ROOT = Path(__file__).resolve().parents[2]
STUDY = Path("binance-btcusdt-futures-research/paper_validation_20261031")
DATA = Path("binance-btcusdt-futures-research/data/paper_validation_20261031")

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()

def iso(ms):
    return pd.to_datetime(int(ms),unit="ms",utc=True).isoformat()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root",type=Path,required=True)
    p.add_argument("--output",type=Path,default=Path(__file__).with_name("archived_study_replay.json"))
    args = p.parse_args()
    source = args.source_root.resolve()
    output = args.output.resolve()
    if output.parent != Path(__file__).resolve().parent:
        raise ValueError("output must remain in validation directory")
    paths = {"market":source/DATA/"market_1m.npz", "targets":source/DATA/"target_paths.npz",
             "manifest":source/STUDY/"results/run_manifest.json",
             "performance":source/STUDY/"results/performance.csv"}
    hashes = {k:sha(v) for k,v in paths.items()}
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert hashes["market"] == manifest["inputs"]["data"], "market hash mismatch"
    expected_perf = next(v for k,v in manifest["artifacts"].items()
                         if k.replace("\\","/") == "results/performance.csv")
    assert hashes["performance"] == expected_perf, "historical performance hash mismatch"
    config = manifest["inputs"]["config"]
    with np.load(paths["market"],allow_pickle=False) as z:
        market = {k:z[k] for k in z.files}
    with np.load(paths["targets"],allow_pickle=False) as z:
        targets = {k:z[k] for k in z.files}
    ts = market["timestamp"]
    start_ms = pd.Timestamp(config["start"],tz="UTC").value//10**6
    end_ms = pd.Timestamp(config["end_exclusive"],tz="UTC").value//10**6
    test_ms = pd.Timestamp(config["test_start"],tz="UTC").value//10**6
    assert np.array_equal(ts,np.arange(start_ms,end_ms,60000)), "market minute coverage mismatch"
    start = int(np.searchsorted(ts,test_ms)); end = len(ts)
    execution = targets.pop("execution")
    expected_execution = np.arange(1440,len(ts)-config["execution_delay_minutes"],
                                   config["decision_minutes"])
    expected_execution = expected_execution[ts[expected_execution]>=test_ms]+config["execution_delay_minutes"]
    assert np.array_equal(execution,expected_execution), "stored execution schedule mismatch"
    old = pd.read_csv(paths["performance"])
    assert len(targets)==16 and len(old)==48 and not old.duplicated(["method","slippage_bps"]).any()
    assert set(old["method"])==set(targets)
    assert set(old["slippage_bps"])==set(config["slippage_bps"])
    engine_path = ROOT/STUDY/"engine.py"
    engine_hash = sha(engine_path)
    spec = importlib.util.spec_from_file_location("archived_replay_engine",engine_path)
    engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = engine
    spec.loader.exec_module(engine)
    rows = []
    for prior in old.to_dict("records"):
        name,slip = prior["method"],prior["slippage_bps"]
        raw = targets[name]
        target_paths = raw[None,:] if raw.ndim==1 else raw
        assert target_paths.ndim==2 and target_paths.shape[1]==len(execution)
        assert len(target_paths)==int(prior["accounts"]) and np.all(np.isfinite(target_paths))
        aggregate=np.zeros(end-start); exposure=np.zeros(end-start); total=np.zeros(10)
        for target in target_paths:
            equity,exp,qty,totals=engine.replay(market,execution,target,start,end,config,slip_bps=slip)
            aggregate+=equity; exposure+=exp/len(target_paths); total+=totals
        initial=config["initial_equity"]*len(target_paths)
        stats,_,_=engine.performance(aggregate,exposure,total,initial,ts[start:end])
        ledger = initial+total[5]-total[1]-total[2]-total[3]+total[9]
        assert abs(aggregate[-1]-ledger)<1e-5,"ledger does not reconcile"
        differences = {k:float(v-prior[k]) for k,v in stats.items()
                       if k in prior and v is not None and pd.notna(prior[k])}
        # Old CSV did not expose deficit/floor credit. Infer the unexplained
        # equity residual, do not relabel it as directly observed old shortfall.
        prior_residual = prior["net_pnl_usdt"]-(prior["gross_pnl_usdt"]-prior["fees_usdt"]
                         -prior["slippage_usdt"]-prior["funding_paid_usdt"])
        rows.append(dict(method=name,slippage_bps=slip,accounts=len(target_paths),
            old_terminal_equity=initial+prior["net_pnl_usdt"],
            new_terminal_equity=float(aggregate[-1]),
            old_net_pnl=prior["net_pnl_usdt"],new_net_pnl=stats["net_pnl_usdt"],
            old_liquidation_count=int(prior["liquidation_proxy_count"]),
            new_liquidation_count=stats["liquidation_proxy_count"],
            old_unexplained_ledger_residual=prior_residual,
            new_floor_credit=stats["liquidation_floor_credit_usdt"],
            new_ledger_residual=float(aggregate[-1]-ledger),
            differences=differences,new_metrics=stats))
        print(name,slip,differences["net_pnl_usdt"],flush=True)
    assert hashes=={k:sha(v) for k,v in paths.items()},"source changed while running"
    assert sha(engine_path)==engine_hash,"engine changed while running"
    result = dict(status="complete",base_commit=subprocess.check_output(
        ["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),engine_sha256=engine_hash,
        engine_is_modified_from_historical=engine_hash!=manifest["inputs"]["sources"]["engine.py"],
        hashes=hashes,market_manifest_hash_verified=True,performance_manifest_hash_verified=True,
        target_hash_provenance="computed now; historical manifest did not bind target_paths.npz",
        inputs_unchanged_after_run=True,environment=dict(python=sys.version,numpy=np.__version__,
            pandas=pd.__version__,numba=numba.__version__,platform=platform.platform()),
        period=dict(market_start=iso(ts[0]),market_end_exclusive=iso(ts[-1]+60000),
                    test_start=iso(ts[start]),test_end_exclusive=iso(ts[-1]+60000),
                    market_minutes=len(ts),test_minutes=end-start,decision_count=len(execution)),
        conditions=len(rows),strategies=len(targets),slippage_bps=config["slippage_bps"],
        summary=dict(max_abs_pnl_difference=max(abs(r["differences"]["net_pnl_usdt"]) for r in rows),
            changed_pnl_conditions_at_1e_6=sum(abs(r["differences"]["net_pnl_usdt"])>1e-6 for r in rows),
            changed_liquidation_conditions=sum(r["old_liquidation_count"]!=r["new_liquidation_count"] for r in rows),
            total_new_floor_credit=sum(r["new_floor_credit"] for r in rows)),
        limitations=["Stored targets reused; no training, feature construction, acquisition, bootstrap or fault-study rerun.",
            "Funding and liquidation remain flat-rate minute-OHLC proxies, not venue fill verification.",
            "Conditions reuse the same data; 48 conditions are not independent statistical samples.",
            "Original target provenance is not attested by historical manifest; its present hash is recorded.",
            "Old floor credit was not logged; old residual is only inferred from saved ledger totals."],
        rows=rows)
    output.write_text(json.dumps(result,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(result["summary"]),flush=True)

if __name__=="__main__":
    main()
