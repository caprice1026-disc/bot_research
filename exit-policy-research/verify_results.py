"""Verify hashes, accounting, shared entries, frozen weights, and one seed retraining."""
import argparse
import json
from pathlib import Path
import time

import research as r
import numpy as np
import pandas as pd
import torch


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--retrain-seed", type=int)
    args = p.parse_args()
    out = args.output.resolve()
    cfg = json.loads((out / "config.json").read_text(encoding="utf-8"))
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    guard = r.memory_guard(cfg["memory_limit_mb"])
    started = time.perf_counter()
    manifest = json.loads((out / "artifact_manifest.json").read_text(encoding="utf-8"))
    for path, expected in manifest.items():
        assert r.sha(out / path) == expected, path
    runtime = json.loads((out / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["source_sha256"] == r.sha(Path(r.__file__)), "source changed since training"
    data_manifest = json.loads((out / "data_manifest.json").read_text(encoding="utf-8"))
    data = {}
    for a, v in data_manifest.items():
        assert r.sha(v["path"]) == v["sha256"]
        data[a] = r.load_data(v["path"])
    splits, edges, purged, _ = r.build_splits(data, cfg)
    scaler = r.fit_scaler(splits["train"])
    frozen = json.loads((out / "scaler.json").read_text(encoding="utf-8"))
    for k in ("mean", "std"):
        np.testing.assert_array_equal(scaler[k], frozen[k])
    all_rows = pd.read_csv(out / "metrics.csv")
    assert len(all_rows) == len(cfg["seeds"]) * 20
    evaluated = 0
    accounting_files = 0
    first_entries = {}
    for seed in cfg["seeds"]:
        sd = out / f"seed_{seed}"
        models = {}
        for name in ("mlp", "raw_rl", "mlp_rl"):
            saved = torch.load(sd / f"{name}.pt", map_location="cpu", weights_only=True)
            assert saved["checkpoint"] == "final_fixed_budget_no_test_selection"
            models[name] = r.ActorCritic(cfg)
            models[name].load_state_dict(saved["state_dict"])
            models[name].eval()
        tr = json.loads((sd / "training.json").read_text(encoding="utf-8"))
        for key in ("updates", "sampled_episodes", "slot_5m_environment_steps"):
            assert tr["raw_rl"][key] == tr["mlp_rl"][key]
        if args.retrain_seed == seed:
            mlp, _ = r.train_mlp(splits["train"], scaler, cfg, seed)
            raw, _ = r.train_rl(splits["train"], scaler, cfg, seed)
            warm, _ = r.train_rl(splits["train"], scaler, cfg, seed, mlp)
            for name, model in [("mlp", mlp), ("raw_rl", raw), ("mlp_rl", warm)]:
                for key, value in model.state_dict().items():
                    assert torch.equal(value, models[name].state_dict()[key]), f"retrain mismatch {name}:{key}"
        for split, i in [("val", 1), ("test", 2)]:
            for name in ("fixed_max", "fixed_12h", "mlp", "raw_rl", "mlp_rl"):
                got, _ = r.evaluate(data, splits[split], edges[i:i+2], cfg, name, models.get(name), scaler)
                expected = json.loads((sd / f"{split}_base_{name}" / "metrics.json").read_text(encoding="utf-8"))
                for key in ("net_return_pct", "max_drawdown_pct", "sharpe_5m_365d", "total_cost", "exposure_mean"):
                    assert abs(got[key] - expected[key]) < 1e-9, (seed, name, key)
                evaluated += 1
        for folder in sorted(sd.glob("*_base_*")) + sorted(sd.glob("test_double_cost_*")) + sorted(sd.glob("test_delay_5m_*")):
            if not folder.is_dir():
                continue
            eq = pd.read_csv(folder / "equity.csv")
            trades = pd.read_csv(folder / "trades.csv")
            np.testing.assert_allclose(eq.equity, eq.cash + eq.market_value, atol=3e-8)
            assert eq.cash.min() >= -1e-8
            assert eq.exposure.between(0, 1 + 1e-10).all()
            np.testing.assert_allclose(trades.gross_pnl - trades.cost, trades.net_pnl, atol=1e-7)
            assert abs(eq.equity.iloc[-1] - cfg["initial_cash"] - trades.net_pnl.sum()) < 1e-6
            split = folder.name.split("_")[0]
            entries = list(zip(trades.asset, trades.entry_ms))
            if split in first_entries:
                assert first_entries[split] == entries, "entry opportunity depends on policy"
            else:
                first_entries[split] = entries
            accounting_files += 1
    r.dump(args.evidence, {"status": "passed", "artifact_hashes_verified": len(manifest),
          "frozen_policy_re_evaluations": evaluated, "accounting_and_common_entry_runs": accounting_files,
          "retrained_seed": args.retrain_seed, "bitwise_weights_equal": args.retrain_seed is not None,
          "split_counts": {s: len(es) for s, es in splits.items()}, "purged_candidates": len(purged),
          "source_hash_matches": True, "memory_guard": guard,
          "seconds": time.perf_counter() - started})
    print(args.evidence.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
