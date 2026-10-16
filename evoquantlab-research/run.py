"""Offline CLI: prepare -> search (development only) -> evaluate (one holdout batch)."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
from limits import apply_limits


def code_hashes():
    from engine import file_hash
    root = Path(__file__).resolve().parent
    return {name: file_hash(root / name) for name in ("engine.py", "run.py", "limits.py")}


def prepare(args, cfg, limits):
    from engine import aggregate_sources, save_panel, ms, HOUR, write_json, digest, file_hash, iso
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = Path(args.source_dir).resolve() if args.source_dir else (Path(args.settings).resolve().parent / cfg["source_dir"]).resolve()
    write_json(root / "settings.json", cfg)
    # Pre-registration is committed to disk before reading price observations.
    prereg = {"protocol": cfg, "protocol_hash": digest(cfg), "code_hashes": code_hashes(),
              "registered_at_utc": datetime.now(timezone.utc).isoformat(),
              "resources": limits, "llm_calls": 0, "registration_order": "before data aggregation and candidate evaluation",
              "prohibited": ["orders", "credentials", "external calls", "post-test tuning"],
              "adaptation": "BTC/ETH time-series, long-only spot/cash, offline proposer; not original paper replication"}
    write_json(root / "pre_registration.json", prereg)
    panel, evidence = aggregate_sources(source, cfg)
    split = int((ms(cfg["test_start"]) - panel.time[0]) // HOUR)
    development = panel.prefix(split)
    from engine import Panel
    test = Panel(panel.time[split:], panel.symbols, {k: v[split:] for k, v in panel.values.items()})
    save_panel(root / "development.npz", development)
    save_panel(root / "sealed_test.npz", test)
    evidence.update({"development_sha256": file_hash(root / "development.npz"), "sealed_test_sha256": file_hash(root / "sealed_test.npz"),
                     "development_hours": len(development.time), "sealed_test_hours": len(test.time),
                     "development_end_exclusive": iso(development.time[-1] + HOUR), "test_start": iso(test.time[0]),
                     "source_dir": str(source), "incomplete_hours": 0, "missing_bars": 0})
    write_json(root / "data_manifest.json", evidence)
    print(json.dumps({"prepared": str(root), "development_hours": len(development.time), "sealed_test_hours": len(test.time),
                      "resources": limits}), flush=True)


def assert_contract(root, cfg):
    from engine import digest, file_hash
    prereg = json.loads((root / "pre_registration.json").read_text(encoding="utf-8"))
    if prereg["protocol_hash"] != digest(cfg) or prereg["code_hashes"] != code_hashes():
        raise ValueError("Configuration or implementation changed after registration; refuse experiment")
    data = json.loads((root / "data_manifest.json").read_text(encoding="utf-8"))
    if file_hash(root / "development.npz") != data["development_sha256"]:
        raise ValueError("Development hash changed")
    return prereg, data


def do_search(args, cfg, limits):
    from engine import load_panel, search, write_json
    root = Path(args.output).resolve()
    assert_contract(root, cfg)
    if (root / "freeze.json").exists() or (root / "holdout_access.json").exists():
        raise ValueError("Search forbidden after freeze/holdout access")
    install_holdout_guard(root / "sealed_test.npz")
    # Search process opens ONLY this NPZ, never the sealed_test.npz file.
    panel = load_panel(root / "development.npz")
    directory = root / "search" / f"{args.method}_seed{args.seed}"
    result = search(panel, cfg, args.method, args.seed, directory)
    write_json(directory / "resource_limits.json", dict(limits, python_holdout_open_guard=True))
    print(json.dumps({"method": args.method, "seed": args.seed, "fitness": result["winner"]["fitness"],
                      "admitted": result["winner"]["admitted"], "evaluations": result["evaluations"], "llm_calls": 0}), flush=True)


def install_holdout_guard(forbidden):
    """Deny Python file opens of sealed input throughout this search process."""
    forbidden = Path(forbidden).resolve()
    def audit(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve() == forbidden:
                raise PermissionError("Holdout file cannot be opened by search process")
    sys.addaudithook(audit)


def save_account(directory, result, panel, indices, cfg, delay, fee_bps, arm):
    import numpy as np
    import pandas as pd
    from engine import HOUR, iso, write_json
    directory.mkdir(parents=True, exist_ok=False)
    starts = panel.time[indices]
    prices = panel.values["open"][indices]
    end_prices = panel.values["open"][indices[-1] + 1]
    rows, all_movements = [], []
    for i, when in enumerate(starts):
        for j, symbol in enumerate(panel.symbols):
            notional = result["notionals"][i, j]
            movement = {"timestamp": iso(when), "signal_available_at": iso(when - delay * HOUR) if arm != "buy_hold" else iso(when),
                        "symbol": symbol, "quantity_delta": float(notional / prices[i, j]), "price": float(prices[i, j]),
                        "notional": float(abs(notional)), "fee": float(result["fees"][i, j]),
                        "side": "buy" if notional > 0 else "sell", "reason": "rebalance" if arm != "buy_hold" else "initial_purchase"}
            if notional != 0:
                all_movements.append(movement)
            if abs(notional) > result["before"][i] * 1e-10:
                rows.append(movement)
    for j, symbol in enumerate(panel.symbols):
        notional = result["liquidation_notional"][j]
        movement = {"timestamp": iso(starts[-1] + HOUR), "signal_available_at": "", "symbol": symbol,
                    "quantity_delta": float(-notional / end_prices[j]), "price": float(end_prices[j]),
                    "notional": float(notional), "fee": float(result["liquidation_fees"][j]), "side": "sell", "reason": "final_liquidation"}
        if notional != 0:
            all_movements.append(movement)
        if notional > cfg["initial_equity"] * 1e-10:
            rows.append(movement)
    pd.DataFrame(rows, columns=["timestamp", "signal_available_at", "symbol", "quantity_delta", "price", "notional", "fee", "side", "reason"]).to_csv(
        directory / "trades.csv", index=False, float_format="%.14g")
    pd.DataFrame(all_movements, columns=["timestamp", "signal_available_at", "symbol", "quantity_delta", "price", "notional", "fee", "side", "reason"]).to_csv(
        directory / "account_movements.csv", index=False, float_format="%.17g")
    equity = pd.DataFrame({"timestamp": [iso(starts[0])] + [iso(t + HOUR) for t in starts],
                           "equity": np.concatenate(([cfg["initial_equity"]], result["equity"])),
                           "net_return": np.concatenate(([0.0], result["returns"])),
                           "holding_pnl": np.concatenate(([0.0], result["pnl"])),
                           "fees": np.concatenate(([0.0], result["fees"].sum(axis=1)))})
    equity.loc[len(equity) - 1, "fees"] += result["liquidation_fees"].sum()
    equity.to_csv(directory / "equity.csv", index=False, float_format="%.14g")
    write_json(directory / "metrics.json", dict(result["metrics"], fee_bps=fee_bps, delay_hours=delay, arm=arm))
    # Independent cash/share replay from the saved trade ledger validates real quantities.
    cash, shares = cfg["initial_equity"], {s: 0.0 for s in panel.symbols}
    for row in all_movements:
        q = row["quantity_delta"]
        cash -= q * row["price"] + row["fee"]
        shares[row["symbol"]] += q
        if cash < -1e-6 or min(shares.values()) < -1e-6:
            raise ArithmeticError("Saved ledger requires borrowing/shorting")
    residual = cash - result["metrics"]["final_equity"]
    if abs(residual) > 1e-6 or max(abs(x) for x in shares.values()) > 1e-6:
        raise ArithmeticError("Saved ledger does not reconcile after liquidation")
    write_json(directory / "ledger_audit.json", {"cash_after_liquidation": cash, "remaining_shares": shares,
               "equity_residual": residual, "borrowed_cash": False, "short_positions": False, "passed": True,
               "full_movement_rows": len(all_movements), "material_trade_rows": len(rows),
               "audit_ledger": "account_movements.csv includes all nonzero numerical movements; trades.csv uses original 1e-10 equity materiality threshold"})


def evaluate(args, cfg, limits):
    import numpy as np
    import pandas as pd
    from engine import (load_panel, join_panels, file_hash, digest, write_json, make_features, Evaluator, normalize,
                        make_weights, fold_indices, forward_labels, temporal_ic, simulate, HOUR, ms, iso)
    root = Path(args.output).resolve()
    prereg, data = assert_contract(root, cfg)
    expected = cfg["cycles"] * cfg["evaluations_per_cycle"]
    selections = []
    artifacts = {}
    for method in cfg["methods"]:
        for seed in cfg["seeds"]:
            directory = root / "search" / f"{method}_seed{seed}"
            result = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
            if result["evaluations"] != expected or result["test_observations_seen"] != 0:
                raise ValueError("Incomplete budget or search contamination")
            selections.append(result)
            for name in ("selection.json", "trials.jsonl", "attempts.jsonl"):
                artifacts[f"search/{directory.name}/{name}"] = file_hash(directory / name)
    # All stress settings and all seeds are frozen together, before the only holdout read.
    freeze = {"protocol_hash": prereg["protocol_hash"], "code_hashes": code_hashes(), "data": data,
              "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
              "selections": selections, "search_artifact_hashes": artifacts, "resources": limits,
              "stress_grid": {"fee_bps": cfg["cost_sensitivity_bps"], "delay_hours": cfg["delay_sensitivity_hours"]},
              "selection_rule": "Each method/seed's dev winner; never select a seed or alter settings using test",
              "test_access_count_before_freeze": 0}
    freeze["admission_hash"] = digest(freeze)
    with (root / "freeze.json").open("x", encoding="utf-8") as handle:
        json.dump(freeze, handle, sort_keys=True, indent=2)
    with (root / "holdout_access.json").open("x", encoding="utf-8") as handle:
        json.dump({"read_count": 1, "admission_hash": freeze["admission_hash"], "reason": "single predeclared batch", "retuning": False}, handle, indent=2)
    if file_hash(root / "sealed_test.npz") != data["sealed_test_sha256"]:
        raise ValueError("Holdout data hash changed")
    panel = join_panels(load_panel(root / "development.npz"), load_panel(root / "sealed_test.npz"))
    features = make_features(panel)
    evaluator = Evaluator(features, cfg)
    indices = fold_indices(panel, cfg["test_start"], cfg["test_end"], cfg)
    returns = panel.values["open"][indices + 1] / panel.values["open"][indices] - 1
    labels = forward_labels(panel, cfg["label_horizon_hours"], cfg["execution_delay_hours"])
    label_mask = (panel.time >= ms(cfg["test_start"]) + cfg["embargo_hours"] * HOUR) & (
        panel.time + (1 + cfg["execution_delay_hours"] + cfg["label_horizon_hours"]) * HOUR < ms(cfg["test_end"]))
    rows, ic_rows = [], []
    for selection in selections:
        method, seed, winner = selection["method"], selection["seed"], selection["winner"]
        score = normalize(evaluator(winner["tree"]), winner["normalizer"])
        ic_rows.append({"method": method, "seed": seed, "temporal_ic_24h": temporal_ic(score, labels, label_mask),
                        "predictive_samples_per_symbol": int(label_mask.sum()), "cross_sectional_ic": "not used: two-asset degeneracy"})
        for delay in cfg["delay_sensitivity_hours"]:
            weights = make_weights(score, features, cfg, delay)[indices]
            for fee in cfg["cost_sensitivity_bps"]:
                result = simulate(weights, returns, fee, cfg["initial_equity"])
                row = dict(result["metrics"], method=method, seed=seed, fee_bps=fee, delay_hours=delay,
                           candidate_id=winner["id"], admitted_on_development=winner["admitted"],
                           dev_fitness=winner["fitness"], start=iso(panel.time[indices[0]]), end=iso(panel.time[indices[-1]] + HOUR))
                rows.append(row)
                if fee == cfg["fee_bps"] and delay == cfg["execution_delay_hours"]:
                    save_account(root / "backtests" / f"{method}_seed{seed}", result, panel, indices, cfg, delay, fee, method)
                    # Gate failure is a cash policy, kept separate from diagnostic candidate performance.
                    row["admitted_policy_net_return"] = row["net_return"] if winner["admitted"] else 0.0
                    row["admitted_policy_status"] = "candidate" if winner["admitted"] else "cash_gate_rejected"
        print(f"holdout batch evaluated {method} seed={seed}", flush=True)
    # True buy and hold: weights drift with price; no hourly rebalancing.
    prices = panel.values["open"][indices]
    relative = prices / prices[0]
    bh_weights = relative / relative.sum(axis=1, keepdims=True)
    for fee in cfg["cost_sensitivity_bps"]:
        result = simulate(bh_weights, returns, fee, cfg["initial_equity"])
        rows.append(dict(result["metrics"], method="buy_hold", seed=-1, fee_bps=fee, delay_hours=1,
                         candidate_id="50_50_initial_drift", admitted_on_development=True,
                         dev_fitness=None, start=iso(panel.time[indices[0]]), end=iso(panel.time[indices[-1]] + HOUR)))
        if fee == cfg["fee_bps"]:
            save_account(root / "backtests" / "buy_hold", result, panel, indices, cfg, 0, fee, "buy_hold")
    pd.DataFrame(rows).to_csv(root / "metrics.csv", index=False, float_format="%.14g")
    write_json(root / "metrics.json", rows)
    write_json(root / "temporal_ic.json", ic_rows)
    real = [s for s in selections if s["method"] == "dual_gp"]
    null = [s["winner"]["fitness"] for s in selections if s["method"] == "noise_gp"]
    audit = {"null_searches": len(null), "trials_per_search": expected, "null_best_fitness": null,
             "selection_p_diagnostic": [{"seed": s["seed"], "real_best": s["winner"]["fitness"],
                "p": (1 + sum(x >= s["winner"]["fitness"] for x in null)) / (1 + len(null))} for s in real],
             "minimum_attainable_p": 1 / (1 + len(null)), "statistical_significance_claimed": False,
             "caveat": "Five complete adaptive block-permutation searches only; correlated seeds and few blocks make this exploratory, not calibrated inference"}
    write_json(root / "selection_bias_audit.json", audit)
    create_report(root, rows, selections, audit, cfg)
    verify(root)


def create_report(root, rows, selections, audit, cfg):
    import numpy as np
    from engine import write_json
    base = [r for r in rows if r["fee_bps"] == cfg["fee_bps"] and r["delay_hours"] == cfg["execution_delay_hours"]]
    summary = {}
    for method in cfg["methods"] + ["buy_hold"]:
        sample = [r for r in base if r["method"] == method]
        summary[method] = {key: {"mean": float(np.mean([r[key] for r in sample])),
                                "min": float(min(r[key] for r in sample)), "max": float(max(r[key] for r in sample))}
                           for key in ("net_return", "max_drawdown", "sharpe", "turnover_one_way", "trades", "cost")}
        summary[method]["development_admitted_count"] = sum(r["admitted_on_development"] for r in sample)
        summary[method]["runs"] = len(sample)
    write_json(root / "summary.json", summary)
    lines = ["# EvoQuantLab-inspired offline adaptation 実験報告", "",
             "LLM呼出は0回。これはBTC/ETH現物の時系列因子探索への適応版であり、原論文の再現実験、LLMの有効性検証、実運用利益ではない。", "",
             "## 固定条件", "",
             "Binance現物の既存CHECKSUM付きデータを読取再利用。2024-06-01〜2025-08-01未満の5分足を、欠損補間せず12本ずつ1時間足に集約した。6月は特徴量の準備期間、訓練は2024-07-01〜2025-02-01、検証は2/3/4月の3区間、未使用テストは2025-05-01〜2025-08-01未満。全区間はUTC・右端を含まない。", "",
             "24h先ラベルと1h追加約定遅延・バー確定の1hを合わせて26hを各境界末尾から除外し、各評価区間先頭にも26hのembargoを置く。実際のテスト約定区間は2025-05-02 02:00〜2025-07-30 22:00 UTC（2156h）。各foldを現金から開始し末尾で全清算する。72h平滑化の履歴は過去から継承できるが、fold間に持高を繰り越さない。固定最大保有期間は設けず、逐次配分更新とfold末尾の清算で会計を閉じる。", "",
             "各因子の平均・標準偏差はpurge後のtrainだけでfitし凍結。各銘柄の正のtanh(score)を最大50%まで買い、過去168hボラティリティで縮小、72h移動平均する。空売り・借入・レバレッジ・金利・funding・清算・価格リターンのwinsorizeは使わない。確定した足の終値情報は1h追加待機後の始値で約定する。片道5bpは手数料とスリッページを合わせた仮定で、実際の市場インパクト・板・最小数量は未モデル化。", "",
             "3cycles×128=384評価/seed/手法、共有5seed（11,23,37,53,71）。固定GP、次cycleだけ探索分布を変えるoffline dual GP、独立random式、168hブロック単位で検証リターンを並べ替えて探索全体を再実行するnoise GPを比較。予算・split・式木depth4/nodes15・population24・閾値・因子語彙は適応版独自の事前設定である。全方式で同じ30個の正負の単因子starterも予算内に含める。", "",
             "GPはトーナメント・部分木交叉・変異・構文重複排除・familyごとのeliteと相関0.95超の繁殖archive除外を使う。相関除外は繁殖の多様性制約で、最終dev winnerは予算内の全評価式から固定目的関数で選ぶ。randomにも同じ構文検証・ノード上限と同じ評価回数を課す。棄却された生成attemptも保存し、1評価あたり最大32attemptで停止する。外側の決定論的proposerは開発期間のfamily別平均fitnessから次cycleのfamily重みだけを0.5〜2へ変更し、採点式・ゲート・会計を変更しない。noiseは同じdual手順をnullリターンに対して走らせる。", "",
             "探索目的は3foldの費用後Sharpeの最小値−0.01×式木ノード数。採用ゲートは全fold Sharpe>0、取引>=10、DD>=−20%。テストは凍結したdev winner全seedの診断評価として実行し、ゲート棄却時の採用方針は現金とする。テストを見てseed/方式/費用/遅延を選び直していない。", "",
             "## 未使用テストの結果", "",
             "表は5bp・1h追加遅延。探索方式は5seedの平均（純収益の範囲も表示）、B&Hは初期50/50購入後リバランスしない1本。DDは負値。tradeは銘柄ごとの約定件数で往復回数ではない。turnoverは片道約定額/直前資産の累計。Sharpeはリスクフリー0、1hリターンの平均/標準偏差×√8760で、年率化は短期間からの推計。", "",
             "|方式|純収益平均 [最小,最大]|最大DD平均|Sharpe平均|turnover平均|trades平均|費用平均USDT|dev採用|", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for method, s in summary.items():
        r = s["net_return"]
        lines.append(f"|{method}|{r['mean']:.2%} [{r['min']:.2%}, {r['max']:.2%}]|{s['max_drawdown']['mean']:.2%}|{s['sharpe']['mean']:.3f}|{s['turnover_one_way']['mean']:.2f}|{s['trades']['mean']:.0f}|{s['cost']['mean']:.2f}|{s['development_admitted_count']}/{s['runs']}|")
    lines += ["", "主表は選択された候補そのものの診断結果である。`metrics.csv`の`admitted_on_development`と`admitted_policy_net_return`で、ゲート通過候補と、棄却後に現金となる運用方針を区別する。ゼロ取引・現金を収益性の成功とは扱わない。", "",
              "## 費用・約定遅延感度", "",
              "全候補を0/5/10/20bp×追加遅延1/2/4hの事前宣言済み12条件で同じ一括テストに含めた。`metrics.csv`/`metrics.json`に全結果を保存。これらの条件を成績によって選択していない。", "",
              "|方式|10bp・1h純収益平均|5bp・2h純収益平均|5bp・4h純収益平均|", "|---|---:|---:|---:|"]
    for method in cfg["methods"]:
        def avg(fee, delay):
            return np.mean([r["net_return"] for r in rows if r["method"] == method and r["fee_bps"] == fee and r["delay_hours"] == delay])
        lines.append(f"|{method}|{avg(10, 1):.2%}|{avg(5, 2):.2%}|{avg(5, 4):.2%}|")
    lines += ["", "## 選択バイアスと限界", "",
              f"各noise seedも384評価を最後まで実行した。null探索は5本だけで、選択補正pの最小値は{audit['minimum_attainable_p']:.4f}。`selection_bias_audit.json`のp値は診断用途であり、統計的有意性は主張しない。各月に168hブロックが少なく、seed間も独立した市場標本ではない。random式探索とnoise探索は別の対照である。", "",
              "BTC/ETHの2銘柄だけなのでcross-sectional IC、carrier residualization、多数銘柄ユニバースの再現は行わない。代わりに銘柄ごとの時系列Pearson IC（24h先、重複ラベルあり）を`temporal_ic.json`へ保存した。ICの有意性・因果性を主張しない。過去データ自体は既存研究で使われており、今回の探索には未使用でも研究者や基盤モデルから完全に未知のデータとは保証できない。期間が約3か月と短く、相場局面・資産を跨ぐ一般化は未検証。", "",
              "原論文の外側LLM診断・知識獲得・多数銘柄の検証ゲートを完全実装したものではない。`LLMProposer`は構造化提案のインターフェースのみで、キー探索・通信・SDK呼出は存在しない。JSON式木はallowlistのみ再帰評価し、LLM生成コードを実行しない。本結果をHyperliquid実績や原論文の性能として報告しない。", "",
              "## 再現と監査", "",
              "`pre_registration.json`（データ読取前設定）、`freeze.json`（test読取前候補・normalizer・source/config/data hash）、`holdout_access.json`（1回の一括開封）、全`search/*/trials.jsonl`/`attempts.jsonl`/`selection.json`を保存。探索プロセスにはdevelopment.npzだけを渡す。holdoutを含むpanelは探索APIで拒否する。既存出力へのtest再評価・探索再開も拒否する。", "",
              "`backtests/*/{trades.csv,equity.csv,metrics.json,ledger_audit.json}`は基本費用/遅延の21口座。独立した現金・数量台帳を再生し、借入・空売りなしで最終資産と一致することを確認。`verification.json`と`test_results.txt`を参照。再実行手順はREADME。", "",
              "原典：[EvoQuantLab v2, §3 / Appendix C.2 / G.3](https://arxiv.org/html/2610.17130v2)。探索と方針更新の分離、開発期間だけの探索、同予算null比較、1h遅延・5bp・72h平滑化を参考にし、その他の数値と現物時系列の構成は本適応版の仮定とした。", ""]
    (root / "report_ja.md").write_text("\n".join(lines), encoding="utf-8")


def verify(root):
    import numpy as np
    import pandas as pd
    from engine import write_json, file_hash, digest
    root = Path(root)
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    cfg = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    freeze = json.loads((root / "freeze.json").read_text(encoding="utf-8"))
    frozen_hash = freeze.pop("admission_hash")
    if digest(freeze) != frozen_hash:
        raise ValueError("Freeze admission hash mismatch")
    for relative, expected in freeze["search_artifact_hashes"].items():
        if file_hash(root / relative) != expected:
            raise ValueError("Search artifact changed after freeze")
    checks = []
    for directory in sorted((root / "backtests").iterdir()):
        stats = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        equity = pd.read_csv(directory / "equity.csv")
        trades = pd.read_csv(directory / "trades.csv")
        if not np.isclose(equity.equity.iloc[-1], stats["final_equity"], atol=1e-6):
            raise ValueError("Equity final mismatch")
        if not np.isclose(trades.fee.sum(), stats["cost"], atol=1e-6):
            raise ValueError("Cost ledger mismatch")
        if len(trades) != stats["trades"]:
            raise ValueError("Trade count mismatch")
        if abs(stats["accounting_residual"]) > 1e-6:
            raise ValueError("Account conservation residual")
        if directory.name == "buy_hold" and len(trades) != 4:
            raise ValueError("Buy-and-hold unexpectedly rebalanced")
        checks.append({"arm": directory.name, "passed": True, "trades": len(trades), "cost": stats["cost"]})
    expected = len(cfg["methods"]) * len(cfg["seeds"]) * len(cfg["cost_sensitivity_bps"]) * len(cfg["delay_sensitivity_hours"]) + len(cfg["cost_sensitivity_bps"])
    if len(metrics) != expected:
        raise ValueError("Incomplete stress grid")
    for method in cfg["methods"]:
        for seed in cfg["seeds"]:
            for delay in cfg["delay_sensitivity_hours"]:
                sample = sorted([r for r in metrics if r["method"] == method and r["seed"] == seed and r["delay_hours"] == delay], key=lambda r: r["fee_bps"])
                if any(a["net_return"] + 1e-12 < b["net_return"] for a, b in zip(sample[:-1], sample[1:])):
                    raise ValueError("Fee sensitivity not monotonic for identical targets")
    write_json(root / "verification.json", {"passed": True, "metrics_rows": len(metrics), "accounts": checks,
               "search_evaluations": len(cfg["methods"]) * len(cfg["seeds"]) * cfg["cycles"] * cfg["evaluations_per_cycle"],
               "equal_budget": True, "freeze_hash_valid": True, "fee_monotonicity": True,
               "llm_calls": 0, "actual_orders": 0})
    print(json.dumps({"verification": "passed", "metrics_rows": len(metrics), "accounts": len(checks)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "search", "evaluate", "verify"])
    parser.add_argument("--settings", default=str(Path(__file__).with_name("settings.json")))
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-dir")
    parser.add_argument("--method", choices=["fixed_gp", "dual_gp", "random", "noise_gp"])
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    cfg = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    if cfg["llm_enabled"]:
        raise ValueError("LLM execution not authorized in this offline study")
    limits = apply_limits(cfg["memory_limit_mb"], cfg["cpu_cores"])
    if args.command == "prepare":
        prepare(args, cfg, limits)
    elif args.command == "search":
        if args.method not in cfg["methods"] or args.seed not in cfg["seeds"]:
            raise ValueError("Unregistered method or seed")
        do_search(args, cfg, limits)
    elif args.command == "evaluate":
        evaluate(args, cfg, limits)
    else:
        verify(args.output)


if __name__ == "__main__":
    main()
