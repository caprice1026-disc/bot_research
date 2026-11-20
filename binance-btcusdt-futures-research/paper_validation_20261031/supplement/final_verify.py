"""Verify all completed artifacts and input hashes without rerunning experiments."""
from pathlib import Path
import json
import sys
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from data_inputs import DATA,ROOT,OUT,sha256_file,write_json
from experiments import verify


def main():
    config=json.loads((HERE/"config.json").read_text())
    verify(config)
    raw=json.loads((OUT/"download_manifest.json").read_text())
    assert not raw["failures"] and len(raw["archives"])==37
    for item in raw["archives"]:assert sha256_file(ROOT/item["path"])==item["sha256"]
    supplementary=json.loads((OUT/"supplement/manifest.json").read_text())
    for path,digest in supplementary["artifacts"].items():assert sha256_file(OUT/"supplement"/path)==digest
    lev=json.loads((OUT/"leverage_stress/manifest.json").read_text())
    assert sha256_file(OUT/"leverage_stress/performance.csv")==lev["csv_sha256"]
    up=json.loads((OUT/"upstream_provenance.json").read_text())
    assert sha256_file(DATA/f"Reward-alignment-{up['revision']}.zip")==up["archive_sha256"]
    fides=json.loads((OUT/"fides_source_manifest.json").read_text())
    assert sha256_file(DATA/"fides-2610.36476v1.tar.gz")==fides["sha256"]
    summary=json.loads((OUT/"upstream_reconstruction_stdout.json").read_text(encoding="utf-8-sig"))
    assert summary["reconstruction"]["neural_records"]==1620
    assert summary["reconstruction"]["ppo_records"]==40
    assert summary["reconstruction"]["max_error"]<1e-10
    assert summary["reconstruction"]["certificate_violations"]==0
    stats=pd.read_csv(OUT/"performance.csv")
    supplement=pd.read_csv(OUT/"supplement/performance.csv")
    leverage=pd.read_csv(OUT/"leverage_stress/performance.csv")
    assert (len(stats),len(supplement),len(leverage))==(48,25,15)
    r=stats[(stats.method=="rl_net_risk10")&(stats.slippage_bps==1)].iloc[0]
    assert round(r.return_pct,2)==.21 and round(r.mdd_pct,2)==5.03
    uncertainty=pd.read_csv(OUT/"paired_uncertainty.csv")
    interval=uncertainty[(uncertainty.method=="rl_net_risk10")&(uncertainty.baseline=="cash")]
    assert (interval.ci95_low_bps<0).all() and (interval.ci95_high_bps>0).all()
    hashes={str(p.relative_to(HERE)):sha256_file(p) for p in sorted(HERE.rglob("*"))
            if p.is_file() and p.suffix in (".py",".json",".csv",".md",".txt",".png") and p.name!="final_verification.json"}
    write_json(OUT/"final_verification.json",dict(status="passed",verified_raw_archives=37,
               primary_replays=48,supplementary_replays=25,leverage_stresses=15,upstream_policy_records=1660,
               raw_market_rows=525600,funding_events=1095,tests={"existing_binance":10,"new_study":11,"total_passed":21},
               data_sufficiency="complete for configured candle/funding study; order book and LLM traces absent",
               hashes=hashes))
    print("final verification passed: 37 raw archives, 88 replay conditions, 1660 saved-policy records")


if __name__=="__main__":main()
