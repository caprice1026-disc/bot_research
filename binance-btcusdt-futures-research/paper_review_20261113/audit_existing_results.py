"""Retrospective rejection screen on saved results, NOT a fresh holdout test.

No models or market replays are rerun. Thresholds below were chosen after the
old results had been seen, so they cannot certify profitability or false-positive
control. They illustrate which existing claims fail basic acceptance checks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "paper_validation_20261031"
OUT = HERE / "results"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def weekly_cluster_interval(dates, normalized_pnl, alpha=.05, trials=10000):
    """ISO-week cluster bootstrap of daily cash PnL / starting capital.

    Draw weeks, preserve all days in a selected week, and divide by the number
    of sampled days. This is not the paper's trade-level options evaluator.
    """
    iso = dates.isocalendar()
    group = iso.year.astype(str) + "-" + iso.week.astype(str)
    frame = pd.DataFrame({"week": group.to_numpy(), "pnl": normalized_pnl})
    weeks = frame.groupby("week", sort=False).pnl.agg(["sum", "count"])
    rng = np.random.default_rng(20261113)
    idx = rng.integers(len(weeks), size=(trials, len(weeks)))
    means = weeks["sum"].to_numpy()[idx].sum(1) / weeks["count"].to_numpy()[idx].sum(1)
    return float(np.quantile(means, alpha)), int(len(weeks))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    frozen = json.loads((OLD / "results/final_verification.json").read_text(encoding="utf-8"))
    checked = 0
    for name, digest in frozen["hashes"].items():
        if sha(OLD / name) != digest:
            raise ValueError(f"Prior study artifact changed: {name}")
        checked += 1
    inputs = []
    rows = []
    for folder, scope in ((OLD / "results", "primary"), (OLD / "results/supplement", "post_hoc_supplement")):
        daily_path, metrics_path = folder / "daily.csv", folder / "performance.csv"
        inputs.extend([daily_path, metrics_path])
        daily = pd.read_csv(daily_path)
        metrics = pd.read_csv(metrics_path)
        for name, frame in daily[daily.slippage_bps == 1.].groupby("method"):
            if name == "cash":
                continue
            frame = frame.sort_values("date")
            dates = pd.DatetimeIndex(pd.to_datetime(frame.date, utc=True))
            assert len(frame) == 273 and dates.is_unique
            assert np.all(np.diff(dates.asi8) == 86400 * 10**9)
            start_capital = frame.equity.iloc[0] / (1 + frame.return_fraction.iloc[0])
            pnl = np.diff(np.r_[start_capital, frame.equity.to_numpy()]) / start_capital
            stats = metrics[metrics.method == name].set_index("slippage_bps")
            assert {1., 3., 5.}.issubset(stats.index)
            base, stress = float(stats.loc[1., "return_pct"]), float(stats.loc[5., "return_pct"])
            assert abs(pnl.sum() * 100 - base) < 1e-8
            lower, weeks = weekly_cluster_interval(dates, pnl)
            first = float(pnl[:len(pnl)//2].sum() * 100)
            second = float(pnl[len(pnl)//2:].sum() * 100)
            gates = dict(positive_total_pnl=base > 0, positive_weekly_lower_bound=lower > 0,
                         positive_high_cost_pnl=stress > 0, both_half_pnl_positive=min(first, second) > 0)
            rows.append(dict(method=name,scope=scope,return_pct=base,high_cost_return_pct=stress,
                             mdd_pct=float(stats.loc[1.,"mdd_pct"]),weekly_lower_mean_pnl_bps=lower*10000,
                             iso_week_clusters=weeks,first_half_pnl_pct_initial=first,second_half_pnl_pct_initial=second,
                             **gates,all_numeric_gates_pass=all(gates.values()),
                             adoption_eligible=False,reason="retrospective_seen_data; new_holdout_required; " + "; ".join(k for k,v in gates.items() if not v)))
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "retrospective_gate_audit.csv", index=False)
    seed_cagr = np.array([33.46, 48.69, 199.45, -3.00, 43.20])
    seed_mdd = np.array([42.50,47.95,35.27,42.48,59.11])
    seed_summary = dict(source="https://arxiv.org/html/2606.04574v3", evidence="arithmetic on author-reported table, not a rerun",
                        seeds=[0,1,42,100,999],cagr_pct=seed_cagr.tolist(),mdd_pct=seed_mdd.tolist(),
                        mean_cagr_pct=float(seed_cagr.mean()),median_cagr_pct=float(np.median(seed_cagr)),
                        mean_cagr_excluding_best_seed_pct=float(np.delete(seed_cagr,np.argmax(seed_cagr)).mean()),
                        positive_seed_count=int((seed_cagr>0).sum()),mean_mdd_pct=float(seed_mdd.mean()))
    (OUT/"pair_seed_arithmetic.json").write_text(json.dumps(seed_summary,indent=2)+"\n",encoding="utf-8")
    manifest = dict(status="complete",date="2026-11-13",scope="retrospective audit only; no new holdout; no LLM calls; no market replay",
                    old_artifact_hashes_verified=checked,methods=len(table),numeric_survivors=int(table.all_numeric_gates_pass.sum()),
                    adoption_eligible=0,bootstrap=dict(unit="ISO-week clusters of daily cash PnL, normalized by initial capital",resamples=10000,
                    lower_quantile=.05,seed=20261113,multiple_testing_adjusted=False),
                    costs=dict(fee_bps_per_side=5,base_slippage_bps=1,stress_slippage_bps=5),
                    notes=["Four illustrative numeric gates, not the full paper criteria.",
                           "Previously inspected periods cannot become unused holdouts by relabeling.",
                           "No multi-year stability or options absolute_edge gate is asserted.",
                           "Shared-strategy arms and methods are not independent replications."],
                    inputs={str(p.relative_to(HERE.parent)):sha(p) for p in inputs},
                    script_sha256=sha(Path(__file__)),
                    outputs={p.name:sha(p) for p in [OUT/"retrospective_gate_audit.csv",OUT/"pair_seed_arithmetic.json"]})
    (OUT/"audit_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:manifest[k] for k in ("status","old_artifact_hashes_verified","methods","numeric_survivors","adoption_eligible")}))
    print(table.loc[table.method.isin(["rl_net_risk10","daily_trend7d","daily_shared75","daily_shared75_cap035"]),
                    ["method","return_pct","high_cost_return_pct","weekly_lower_mean_pnl_bps","both_half_pnl_positive","all_numeric_gates_pass"]].to_string(index=False))
    print(json.dumps(seed_summary))


if __name__ == "__main__":
    main()
