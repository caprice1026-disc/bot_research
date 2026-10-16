"""Replay frozen models after the reviewed terminal-open execution fix. No training."""
import argparse
import ast
import copy
import difflib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import time
import unittest

import research as r
import numpy as np
import pandas as pd
import psutil
import torch


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def node_bodies(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def manifest_check(root):
    manifest = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
    for f, h in manifest.items():
        assert r.sha(root / f) == h, f
    return len(manifest)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    baseline, out = args.baseline.resolve(), args.output.resolve()
    source = Path(r.__file__).resolve()
    before_path = out / "source_before" / "research.py"
    assert before_path.is_file(), "Archive baseline research.py before applying the fix"
    assert not (out / "metrics.csv").exists(), "Use an unused replay output; prior results are immutable"
    cfg = json.loads((baseline / "config.json").read_text(encoding="utf-8"))
    runtime_before = json.loads((baseline / "runtime.json").read_text(encoding="utf-8"))
    assert r.sha(before_path) == runtime_before["source_sha256"]
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    guard = r.memory_guard(cfg["memory_limit_mb"])
    started = time.perf_counter()
    baseline_manifest_hash = r.sha(baseline / "artifact_manifest.json")
    baseline_count = manifest_check(baseline)
    original = load_module("research_before_terminal_fix", before_path)
    before_nodes, after_nodes = node_bodies(before_path), node_bodies(source)
    changed_nodes = [k for k in before_nodes if before_nodes[k] != after_nodes[k]]
    assert changed_nodes == ["make_episode"], changed_nodes
    after = out / "source_after"
    after.mkdir(exist_ok=False)
    tests_path = source.parent / "tests" / "test_research.py"
    shutil.copy2(source, after / "research.py")
    shutil.copy2(tests_path, after / "test_research.py")
    shutil.copy2(Path(__file__), after / "replay_terminal_fix.py")
    for name in ("research.py", "test_research.py"):
        diff = difflib.unified_diff((out / "source_before" / name).read_text(encoding="utf-8").splitlines(True),
                                    (after / name).read_text(encoding="utf-8").splitlines(True),
                                    fromfile=f"before/{name}", tofile=f"after/{name}")
        (out / f"{name}.patch").write_text("".join(diff), encoding="utf-8")
    tm = load_module("terminal_fix_tests", tests_path)
    tm.r = original
    stream_before = io.StringIO()
    old_tests = unittest.TextTestRunner(stream=stream_before, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(tm.TerminalBoundaryTests))
    (out / "tests_before_expected_failures.log").write_text(stream_before.getvalue(), encoding="utf-8")
    assert old_tests.testsRun == 8 and len(old_tests.failures) == 5 and not old_tests.errors
    tm.r = r
    stream_after = io.StringIO()
    new_tests = unittest.TextTestRunner(stream=stream_after, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(tm))
    (out / "tests_after.log").write_text(stream_after.getvalue(), encoding="utf-8")
    assert new_tests.wasSuccessful() and new_tests.testsRun == 25
    tm.r = original
    synthetic_old, _ = tm.TerminalBoundaryTests().episode(120.)
    tm.r = r
    synthetic_new, _ = tm.TerminalBoundaryTests().episode(120.)
    synthetic = {"entry_raw": 100., "terminal_open": 120., "sleeve_initial_cash": 50000.,
                 "before": {"exit": synthetic_old["forced"], **r.cash_result(50000., 100., synthetic_old["forced"][1], cfg)},
                 "after": {"exit": synthetic_new["forced"], **r.cash_result(50000., 100., synthetic_new["forced"][1], cfg)}}
    r.dump(out / "synthetic_gap_reproduction.json", synthetic)
    inputs = json.loads((baseline / "data_manifest.json").read_text(encoding="utf-8"))
    data = {}
    for a, info in inputs.items():
        assert r.sha(info["path"]) == info["sha256"]
        data[a] = r.load_data(info["path"])
    splits, edges, purged, mf = r.build_splits(data, cfg)
    old_splits, old_edges, old_purged, old_mf = original.build_splits(data, cfg)
    assert edges == old_edges and purged == old_purged
    frozen = json.loads((baseline / "scaler.json").read_text(encoding="utf-8"))
    scaler = {k: np.asarray(frozen[k]) for k in ("mean", "std")}
    fitted = r.fit_scaler(splits["train"])
    for k in scaler:
        np.testing.assert_array_equal(scaler[k], fitted[k])
    oracle_changed = 0
    for old, new in zip(old_splits["train"], splits["train"]):
        y_old, j_old = original.oracle_labels(old, cfg)
        y_new, j_new = r.oracle_labels(new, cfg)
        oracle_changed += int(j_old != j_new or not np.array_equal(y_old, y_new))
    episode_audit, scenarios = [], {}
    for stress, mult, delay in [("base", 1, 0), ("double_cost", 2, 0), ("delay_5m", 1, 1)]:
        scenarios[stress] = {}
        for split in ("train", "val", "test"):
            new_es = splits[split] if stress == "base" else [r.make_episode(data[e["asset"]], mf[e["asset"]], e["start"], cfg, e["asset"], mult, delay) for e in splits[split]]
            old_es = old_splits[split] if stress == "base" else [original.make_episode(data[e["asset"]], old_mf[e["asset"]], e["start"], cfg, e["asset"], mult, delay) for e in old_splits[split]]
            scenarios[stress][split] = new_es
            for old, new in zip(old_es, new_es):
                assert old["id"] == new["id"]
                np.testing.assert_array_equal(old["x"], new["x"])
                np.testing.assert_array_equal(old["valid"], new["valid"])
                episode_audit.append({"split": split, "stress": stress, "episode_id": new["id"],
                                      "before_terminal_offset": old["forced"][0], "after_terminal_offset": new["forced"][0],
                                      "before_terminal_raw": old["forced"][1], "after_terminal_raw": new["forced"][1],
                                      "before_reason": old["forced"][2], "after_reason": new["forced"][2],
                                      "terminal_changed": old["forced"] != new["forced"],
                                      "candidate_outcomes_changed": old["candidates"] != new["candidates"]})
    pd.DataFrame(episode_audit).to_csv(out / "episode_terminal_impact.csv", index=False)
    # Config, normalizer, training histories and all model files remain byte-for-byte copies.
    for name in ("config.json", "scaler.json", "data_manifest.json", "split_audit.json", "episodes.csv", "training_budget.csv"):
        shutil.copy2(baseline / name, out / name)
    all_metrics, boot, per_asset, impact, model_hashes = [], {}, [], [], []
    entry_calendar, trade_rows, equity_rows = {}, 0, 0
    for seed in cfg["seeds"]:
        sd = out / f"seed_{seed}"
        sd.mkdir()
        models = {}
        shutil.copy2(baseline / f"seed_{seed}" / "training.json", sd / "training.json")
        for name in ("mlp", "raw_rl", "mlp_rl"):
            old_model = baseline / f"seed_{seed}" / f"{name}.pt"
            shutil.copy2(old_model, sd / f"{name}.pt")
            assert r.sha(old_model) == r.sha(sd / f"{name}.pt")
            model_hashes.append({"seed": seed, "policy": name, "before_sha256": r.sha(old_model), "after_sha256": r.sha(sd / f"{name}.pt")})
            saved = torch.load(old_model, map_location="cpu", weights_only=True)
            models[name] = r.ActorCritic(cfg)
            models[name].load_state_dict(saved["state_dict"])
            models[name].eval()
        boot[seed] = {}
        for split, index in [("val", 1), ("test", 2)]:
            for stress, mult in [("base", 1), ("double_cost", 2), ("delay_5m", 1)]:
                if split == "val" and stress != "base":
                    continue
                for name in ("fixed_max", "fixed_12h", "mlp", "raw_rl", "mlp_rl"):
                    folder_name = f"{split}_{stress}_{name}"
                    folder = sd / folder_name
                    metrics, trades = r.evaluate(data, scenarios[stress][split], edges[index:index+2], cfg, name, models.get(name), scaler, folder, mult)
                    all_metrics.append({"seed": seed, "split": split, "stress": stress, "policy": name, **metrics})
                    for asset, am in metrics["per_asset"].items():
                        per_asset.append({"seed": seed, "split": split, "stress": stress, "policy": name, "asset": asset, **am})
                    if split == "test" and stress == "base" and name in ("mlp", "mlp_rl"):
                        boot[seed][name] = trades
                    previous = baseline / f"seed_{seed}" / folder_name
                    old_metrics = json.loads((previous / "metrics.json").read_text(encoding="utf-8"))
                    delta = {k + "_delta": float(metrics[k] - v) for k, v in old_metrics.items() if isinstance(v, (int, float))}
                    equity = pd.read_csv(folder / "equity.csv")
                    tr = pd.read_csv(folder / "trades.csv")
                    np.testing.assert_allclose(equity.equity, equity.cash + equity.market_value, atol=3e-8)
                    np.testing.assert_allclose(tr.gross_pnl - tr.cost, tr.net_pnl, atol=1e-7)
                    assert equity.cash.min() >= -1e-8 and equity.exposure.between(0, 1 + 1e-10).all()
                    assert abs(equity.equity.iloc[-1] - cfg["initial_cash"] - tr.net_pnl.sum()) < 1e-6
                    entries = list(zip(tr.asset, tr.entry_ms))
                    if split in entry_calendar:
                        assert entries == entry_calendar[split]
                    else:
                        entry_calendar[split] = entries
                    record = {"seed": seed, "split": split, "stress": stress, "policy": name, **delta,
                              **{f"{f}_byte_identical": r.sha(previous / f"{f}.csv") == r.sha(folder / f"{f}.csv") for f in ("trades", "equity")},
                              "metrics_byte_identical": r.sha(previous / "metrics.json") == r.sha(folder / "metrics.json")}
                    impact.append(record)
                    trade_rows += len(tr)
                    equity_rows += len(equity)
        print(f"frozen seed={seed} replayed", flush=True)
    frame = r.summarize(all_metrics, out, cfg)
    r.paired_bootstrap(boot, out, cfg)
    pd.DataFrame(per_asset).to_csv(out / "per_asset_metrics.csv", index=False)
    impact_frame = pd.DataFrame(impact)
    impact_frame.to_csv(out / "old_result_impact.csv", index=False)
    r.dump(out / "weight_hashes.json", model_hashes)
    for name in ("test_comparison.png", "summary_flat.csv", "paired_summary.csv"):
        # All recorded outcomes must be unchanged before reusing a baseline visualization/table.
        if all(v["metrics_byte_identical"] and v["trades_byte_identical"] and v["equity_byte_identical"] for v in impact):
            shutil.copy2(baseline / name, out / name)
    assert manifest_check(baseline) == baseline_count
    assert r.sha(baseline / "artifact_manifest.json") == baseline_manifest_hash
    deltas = impact_frame.filter(regex="_delta$")
    evidence = {"status": "passed", "change": "terminal-open gap SL/TP before max-hold; terminal high/low excluded",
                "changed_top_level_definitions": changed_nodes, "source_before_sha256": r.sha(before_path),
                "source_after_sha256": r.sha(source), "config_unchanged": r.sha(out / "config.json") == r.sha(baseline / "config.json"),
                "training_reexecuted": False, "unchanged_model_files": len(model_hashes),
                "baseline_artifacts_preserved_and_verified": baseline_count,
                "old_boundary_tests": {"run": old_tests.testsRun, "expected_failures": len(old_tests.failures)},
                "all_current_tests": {"run": new_tests.testsRun, "failures": len(new_tests.failures), "errors": len(new_tests.errors)},
                "audited_episode_paths": len(episode_audit), "changed_forced_exits": sum(e["terminal_changed"] for e in episode_audit),
                "changed_candidate_outcomes": sum(e["candidate_outcomes_changed"] for e in episode_audit),
                "changed_train_oracle_labels_episodes": oracle_changed, "reevaluated_runs": len(impact),
                "trades_reconciled": trade_rows, "equity_rows_reconciled": equity_rows,
                "metric_max_abs_delta": float(np.max(np.abs(deltas.to_numpy()))),
                "all_metrics_byte_identical": all(e["metrics_byte_identical"] for e in impact),
                "all_trades_byte_identical": all(e["trades_byte_identical"] for e in impact),
                "all_equity_byte_identical": all(e["equity_byte_identical"] for e in impact),
                "memory_guard": guard, "seconds": time.perf_counter() - started}
    r.dump(out / "fix_evidence.json", evidence)
    assert evidence["metric_max_abs_delta"] == 0 and all(evidence[k] for k in
        ("all_metrics_byte_identical", "all_trades_byte_identical", "all_equity_byte_identical")), "Saved-result impact requires investigation"
    runtime = copy.deepcopy(runtime_before)
    runtime.update({"run_type": "frozen_weight_execution_fix_replay", "source_sha256": r.sha(source),
                    "original_training_source_sha256": runtime_before["source_sha256"],
                    "original_training_runtime": str(baseline / "runtime.json"), "training_reexecuted": False,
                    "wall_seconds": evidence["seconds"], "rss_mb_end": psutil.Process().memory_info().rss / 2**20,
                    "peak_working_set_mb": getattr(psutil.Process().memory_info(), "peak_wset", 0) / 2**20})
    r.dump(out / "runtime.json", runtime)
    rows = frame[frame.split.eq("test") & frame.stress.eq("base")].groupby("policy").net_return_pct.agg(["mean", "std"])
    result_table = "\n".join(f"| {n} | {v['mean']:.6f} | {v['std']:.6f} |" for n, v in rows.iterrows())
    report = f"""# 満了openのgap決済規約: レビュー修正と固定weight再評価

独立レビューのP2を修正した。48h満了openにも既存のgap stop/TPを優先適用し、その後にmax_hold決済とする。上方gapはTP価格に制限、下方gapは不利なopenで売る。満了足のhigh/lowは参照しない。

変更対象はresearch.pyのmake_episodeだけ。学習関数・oracle関数・scaler・config・threshold・各パラメータは変更せず、optimizer/学習は実行していない。15個の保存weightと各training.jsonをそのままコピーし、weight_hashes.jsonに旧新hashを保存した。warmの追加MLP学習とBC anchorがあるため、rawとの差を初期重みだけの効果とは呼ばない。

## 境界再現とテスト

entry raw=100、48h満了open=120、初期slot50,000の合成例では、旧約定120からTP108.054へ修正した。旧期末cash {synthetic['before']['end_cash']:.9f}、新{synthetic['after']['end_cash']:.9f}。これは合成例であり実データの水増しを意味しない。synthetic_gap_reproduction.jsonに費用と数量を保存した。

8境界テストのうち旧ソースで5件が期待通り失敗し、新ソースでは既存17件を含む全25件が通過。満了上方/下方gap、TP/SLちょうどのopen、保護価格内の満了、満了足の後続high/low無視、前の足のstop優先、倍costのfillアンカーを検証した。旧失敗log、新成功log、ソースとテストのbefore/after snapshot、unified diffを保存した。

## 既存結果への影響

train/val/testの全406episodeを通常・倍cost・5m遅延の3条件で監査し、計{evidence['audited_episode_paths']}pathのforced exit/candidate outcomeに変更は{evidence['changed_forced_exits']}/{evidence['changed_candidate_outcomes']}件。train246episodeのoracleラベル変更は{oracle_changed}件。新たな教師を学習更新に使っていない。

保存15モデルと固定2ルールで全{len(impact)}runを再評価した。{trade_rows}取引、{equity_rows:,}資産行を会計照合し、全方式の共通entryと現金待機を確認。旧metrics/trades/equityは全run byte一致、全数値指標の最大絶対差は{evidence['metric_max_abs_delta']:.1f}。元cpu-v2の{baseline_count}成果物hashとmanifestを前後で確認し、旧出力は一切上書きしていない。今回保存結果の利益/DD/Sharpe/費用・結論は変わらない。

| 方策 | 通常test net% mean | seed SD |
| --- | ---: | ---: |
{result_table}

MLP→ACの改善・統計優位は確認されないという結論を維持する。これはSPX short-straddle論文の完全再現ではなく、BTC/ETH現物相当の小規模価格適応である。今回の修正を理由にtestを見た追加学習・parameter探索は行っていない。

## 成果物と再実行

旧結果は ../cpu-v2/、修正後の固定weight再評価は本ディレクトリ。fix_evidence.json、episode_terminal_impact.csv、old_result_impact.csv、weight_hashes.json、tests_after.log、research.py.patch/test_research.py.patchが差分再レビューの入口である。全100runのmetrics/trades/equityと集約、費用・遅延stressを保存した。旧図・補助表は全結果のbyte一致を確認後にコピーした。

repo rootから python -m unittest discover -s exit-policy-research/tests -v で25件を再確認する。python exit-policy-research/verify_results.py --output exit-policy-research/results/terminal-open-fix-v1 --evidence exit-policy-research/results/terminal-open-fix-v1.verification.json で固定weight再評価と会計を確認できる。--retrain-seedは今回使用しない。旧runのsource hashは修正前snapshotと対応し、現行ソースのhashとは区別する。
"""
    (out / "report_ja.md").write_text(report, encoding="utf-8")
    r.dump(out / "artifact_manifest.json", {str(f.relative_to(out)): r.sha(f) for f in sorted(out.rglob("*")) if f.is_file() and f.name != "artifact_manifest.json"})
    print(json.dumps(evidence, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
