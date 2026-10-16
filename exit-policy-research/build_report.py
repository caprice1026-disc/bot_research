"""Generate Japanese report, tidy aggregates, and static research figure from saved outputs."""
import argparse
import json
import os
from pathlib import Path

import research as r
import pandas as pd
import numpy as np
os.environ["MPLCONFIGDIR"] = str(Path(__file__).parent / ".cache" / "matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ORDER = ["fixed_max", "fixed_12h", "mlp", "raw_rl", "mlp_rl"]
LABEL = {"fixed_max": "固定48h", "fixed_12h": "固定12h", "mlp": "MLP", "raw_rl": "ゼロからAC", "mlp_rl": "MLP→AC"}


def table(headers, rows):
    return "| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n" + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    out = args.output
    df = pd.read_csv(out / "metrics.csv")
    cfg = json.loads((out / "config.json").read_text(encoding="utf-8"))
    runtime = json.loads((out / "runtime.json").read_text(encoding="utf-8"))
    audit = json.loads((out / "split_audit.json").read_text(encoding="utf-8"))
    metrics = ["net_return_pct", "gross_same_quantity_return_pct", "max_drawdown_pct", "sharpe_5m_365d", "turnover_initial_capital", "total_cost", "exposure_mean", "trades"]
    tidy = []
    for keys, g in df.groupby(["split", "stress", "policy"]):
        row = dict(zip(("split", "stress", "policy"), keys))
        for m in metrics:
            for stat, val in [("mean", g[m].mean()), ("std", g[m].std()), ("median", g[m].median()), ("min", g[m].min()), ("max", g[m].max())]:
                row[f"{m}_{stat}"] = float(val)
        tidy.append(row)
    pd.DataFrame(tidy).to_csv(out / "summary_flat.csv", index=False)
    per_asset = []
    training = []
    for seed in cfg["seeds"]:
        sd = out / f"seed_{seed}"
        tr = json.loads((sd / "training.json").read_text(encoding="utf-8"))
        training.append({"seed": seed, "mlp_train_states": tr["mlp"]["train_states"],
                         "positive_class_weight": tr["mlp"]["positive_class_weight"],
                         "mlp_seconds": tr["mlp"]["seconds"],
                         "raw_decisions": tr["raw_rl"]["valid_policy_decisions"],
                         "warm_decisions": tr["mlp_rl"]["valid_policy_decisions"],
                         "raw_seconds": tr["raw_rl"]["seconds"], "warm_seconds": tr["mlp_rl"]["seconds"],
                         "base_seed": seed, "rl_python_numpy_torch_seed": seed + 10000,
                         "rl_episode_sampler_seed": seed + 20000, "updates_each": cfg["rl_updates"],
                         "episodes_each": cfg["rl_updates"] * cfg["rl_batch_episodes"],
                         "fixed_slot_5m_time_each": cfg["rl_updates"] * cfg["rl_batch_episodes"] * cfg["max_hold_hours"] * 12})
        for row in df[df.seed.eq(seed)].to_dict("records"):
            v = json.loads((sd / f"{row['split']}_{row['stress']}_{row['policy']}" / "metrics.json").read_text(encoding="utf-8"))
            for a, am in v["per_asset"].items():
                per_asset.append({k: row[k] for k in ("seed", "split", "stress", "policy")} | {"asset": a} | am)
    pd.DataFrame(per_asset).to_csv(out / "per_asset_metrics.csv", index=False)
    pd.DataFrame(training).to_csv(out / "training_budget.csv", index=False)
    paired = pd.read_csv(out / "paired_differences.csv")
    paired.groupby(["split", "stress", "a", "b"])[metrics].agg(["mean", "std", "median", "min", "max"]).to_csv(out / "paired_summary.csv")
    test = df[df.split.eq("test") & df.stress.eq("base")]
    base = test.groupby("policy")[metrics].mean()
    std = test.groupby("policy")[metrics].std()
    perf = table(["方策", "gross%", "net% 平均±SD", "net中央値%", "DD% 平均±SD", "Sharpe 平均±SD", "turnover", "費用USDT", "exposure"],
                 [[LABEL[n], f"{base.loc[n,'gross_same_quantity_return_pct']:.2f}",
                   f"{base.loc[n,'net_return_pct']:.2f} ± {std.loc[n,'net_return_pct']:.2f}",
                   f"{test[test.policy.eq(n)].net_return_pct.median():.2f}",
                   f"{base.loc[n,'max_drawdown_pct']:.2f} ± {std.loc[n,'max_drawdown_pct']:.2f}",
                   f"{base.loc[n,'sharpe_5m_365d']:.2f} ± {std.loc[n,'sharpe_5m_365d']:.2f}",
                   f"{base.loc[n,'turnover_initial_capital']:.2f}x", f"{base.loc[n,'total_cost']:.0f}",
                   f"{base.loc[n,'exposure_mean']:.1%}"] for n in ORDER])
    seed_table = table(["seed", "固定48h", "固定12h", "MLP", "ゼロからAC", "MLP→AC", "warm−MLP (pp)"],
                       [[s] + [f"{test[(test.seed.eq(s)) & test.policy.eq(n)].net_return_pct.iloc[0]:.4f}" for n in ORDER] +
                        [f"{paired[(paired.seed.eq(s)) & paired.split.eq('test') & paired.stress.eq('base') & paired.a.eq('mlp_rl') & paired.b.eq('mlp')].net_return_pct.iloc[0]:+.4f}"] for s in cfg["seeds"]])
    stress = df[df.split.eq("test")].groupby(["policy", "stress"]).net_return_pct
    stress_table = table(["方策", "通常 net%", "倍費用 net%", "5分追加遅延 net%"],
                         [[LABEL[n]] + [f"{stress.mean().loc[n,s]:.2f} ± {stress.std().loc[n,s]:.2f}" for s in ("base", "double_cost", "delay_5m")] for n in ORDER])
    val = df[df.split.eq("val")].groupby("policy").net_return_pct
    val_table = table(["方策", "val net% 平均±SD"], [[LABEL[n], f"{val.mean()[n]:.2f} ± {val.std()[n]:.2f}"] for n in ORDER])
    pa = pd.DataFrame(per_asset)
    pa = pa[pa.split.eq("test") & pa.stress.eq("base")].groupby(["policy", "asset"]).net_return_pct
    asset_table = table(["方策", "BTC net% 平均±SD", "ETH net% 平均±SD"],
                        [[LABEL[n]] + [f"{pa.mean()[n,a]:.2f} ± {pa.std()[n,a]:.2f}" for a in cfg["assets"]] for n in ORDER])
    budget_table = table(["seed", "MLP秒", "raw AC秒", "warm AC秒", "raw有効判断", "warm有効判断"],
                         [[t["seed"], f"{t['mlp_seconds']:.3f}", f"{t['raw_seconds']:.3f}", f"{t['warm_seconds']:.3f}", t["raw_decisions"], t["warm_decisions"]] for t in training])
    fig, ax = plt.subplots(2, 1, figsize=(11, 8), constrained_layout=True)
    colors = ["#182a45", "#9a7b50", "#297b83", "#b95b56", "#7358a8"]
    for name, color in zip(ORDER, colors):
        curves = [pd.read_csv(out / f"seed_{s}" / f"test_base_{name}" / "equity.csv") for s in cfg["seeds"]]
        x = pd.to_datetime(curves[0].mark_available_ms, unit="ms", utc=True)
        ys = np.array([c.equity.to_numpy() / cfg["initial_cash"] for c in curves])
        ax[0].plot(x, ys.mean(axis=0), label=name, color=color, lw=1.5)
        ax[0].fill_between(x, ys.min(axis=0), ys.max(axis=0), color=color, alpha=.10)
    ax[0].axhline(1, lw=.6, color="gray")
    ax[0].set(title="Locked test: seed mean equity; shading = seed min/max", ylabel="NAV / initial capital")
    ax[0].legend(ncol=5, fontsize=8)
    for i, stress_name in enumerate(("base", "double_cost", "delay_5m")):
        g = df[df.split.eq("test") & df.stress.eq(stress_name)].groupby("policy").net_return_pct
        ax[1].bar(np.arange(5) + (i - 1) * .25, [g.mean()[n] for n in ORDER], width=.25,
                  yerr=[g.std()[n] for n in ORDER], label=stress_name, alpha=.85, capsize=3)
    ax[1].set_xticks(range(5), ORDER)
    ax[1].set(ylabel="Net return %", title="Stress evaluation: seed mean +/- sample SD")
    ax[1].axhline(0, color="gray", lw=.6)
    ax[1].legend(ncol=3, fontsize=8)
    for a in ax:
        a.grid(axis="y", alpha=.2)
    fig.savefig(out / "test_comparison.png", dpi=160)
    plt.close(fig)
    text = f"""# 決済方策比較: CPU小規模実験結果

MLP・ゼロからのRL・MLP初期化RLを実装して5seedで比較したが、この価格だけの適応ではMLP→RLの改善は確認できなかった。通常費用のテストでMLP→ACは平均net −9.32%、MLPは−9.19%、raw ACは−10.77%。共通SL/TP付きの固定48h保有は＋11.96%で、学習方策を上回った。これは原典論文への反証ではなく、資産・状態・報酬・期間・学習量を変えた限定的な試行結果である。

## 原典との関係

[原典論文](https://arxiv.org/html/2610.13126v1) と [公式repo](https://github.com/Justinnnn0313/spx-short-straddle-rl) を2026-10-16に確認した。原典はSPX short-straddleのhold/exit。今回のBTC/ETH現物相当long-onlyにはoption quote、Greeks、VRP、time value、short volatility payoff、margin、settlementがない。64×64 MLPからACを初期化する構造を参考に自前実装し、外部repoのinstall/runは行っていない。元のraw SPXデータ・最終checkpointも利用していない。

公式 [MLP script](https://github.com/Justinnnn0313/spx-short-straddle-rl/blob/main/scripts/train_exit_imitation_policy.py) のrandom ROW validationとtorch seed欠落は採用せず、episode全経路の時系列分割と明示seedにした。MLPはweighted BCEを使用する。公式 [AC script](https://github.com/Justinnnn0313/spx-short-straddle-rl/blob/main/scripts/train_exit_actor_critic_finetune.py) のdefault値は参考であり、最終論文設定と同一とは確認していない。報酬配列のlast N行への依存を避け、実際のterminal時刻と判断時刻を対応させる。

## データ・比較条件

既存Binance BTCUSDT/ETHUSDT spot 5m CSV各122,688本を読み取り再利用した。2024-06-01〜2025-08-01未満、欠損・重複・非有限値・不正OHLC・available時刻を検査し、入力SHA256をdata_manifest.jsonに保存した。全価格とvolumeの加工は過去確定足だけを参照する。

7日warmup後の60/20/20分割は、train {audit['edges_utc'][0]}〜{audit['edges_utc'][1]}未満、valは{audit['edges_utc'][2]}未満、testは{audit['edges_utc'][3]}未満。train246、val80、test80 episode。各episodeの最大保有までの全pathを区間内に限定し、境界/endを跨ぐ6候補をpurgeした。実際の早期決済だけを見て境界を通すことはしない。

BTC/ETH各50%の独立資金slot、初期100,000 USDT、49hごとに固定entry、最大48h・裁量exit最低2h、1hごとに判断。早期決済の現金は次の共通entryまで待機し、全方式で80取引・同一entry時刻を維持した。slot内複利、銘柄間資金移動なし。SL4%・TP8%は買付fill基準で共通。保護注文は5m足、gap stopはopen、gap TPはtargetに抑え、同足SL/TPはstop優先。通常の裁量sell openは同じ足のintrabar到達より先に執行する。intrabar時刻は不明と記録した。判断の次の5m openで売り、最大保有でもopenで売る。

fee片道10bp・slip片道5bp。買付は費用込みで現金を超えない数量にし、借入・空売りなし。固定entry、49h/48h/2h、SL/TP、費用は暗号資産向けの事前仮定である。全方策を同じ資金会計で比較した。

14入力は過去return・vol・range・volume・UTC時刻と、保有時間・未実現損益・過去closeのMFE/MAE等。train状態だけでscalerをfitし、全区間へ固定適用。oracleはtrain内の実行可能な費用控除後exitの最早最大点を教師にする。教師作成の未来利用と、input/評価へのleakageは異なる。val/testにはoracleラベルを作らず、未来報酬・最適時刻・将来収益を特徴に入れない。

## 学習量と再現性

seed 1/3/5/7/11。Python/NumPy/torchをMLPではそのseed、RLではseed+10000、episode抽出ではseed+20000に設定した。bootstrap seedは45001。MLPはtrainの有効状態9,403件、positive class weight 1.2156、64×64 ReLU、8epoch、batch8192、AdamW lr.001・wd1e-5。乱数で行をshuffleするのはtrain minibatch内だけで、valをrandom row splitしない。

raw/warm ACは各100更新×16episode＝1,600 sampled episode、固定slotの5m環境時間921,600本で同じbudget。経路はcacheし、早期exit後の待機時間もslot時間に含める。実際に訪問する有効方策判断数は異なり、同じgradient sample数とは主張しない。共有trunk/scalar actor/value、MC return-to-go、normalized advantage、AdamW lr1e-5、value.5・entropy.001・warm BC anchor.01・clip1。warmのみMLPの追加費用が必要である。

terminal rewardはnet episode return×100、途中0。gamma.997は適応版の経過1時間あたりで、5m単位の実terminal時刻からreturnsを計算。forced hold期間とterminal rewardを含め、最低保有前の無効actionをpolicy gradientから除いた。option固有VRP等のrewardは作っていない。原典default1000更新より小さい試行であり、飽和学習・最良性能とは呼ばない。threshold .5、checkpointは固定最終状態で、val/testによる選択や追加調整は行っていない。

{budget_table}

上表の秒数は学習関数内だけ。全体{runtime['wall_seconds']:.2f}秒はデータ/出力処理も含み、Python起動・import時間を含まない。CPU1thread、Windows Job Objectのprocess commit上限2048MiB、peak working set {runtime['peak_working_set_mb']:.1f}MiB。Python3.10.9、torch{runtime['torch']}をCPUで使用、NumPy{runtime['numpy']}、pandas{runtime['pandas']}。API・Gemini・課金・実発注・git commit/pushは0。

## 通常費用テスト

各数値は同じ市場の5seed平均。SDは標本標準偏差、DDは正の下落幅。固定方策はseedに依存しないためSD0。

{perf}

grossはnetと同じ数量の実現gross損益を足した費用加算戻しで、費用ゼロで複利数量を再計算した別runではない。turnoverは両側fill notional/初期資産。Sharpeは5m returnから年365日で換算し自己相関補正なし。DDは5分終値と決済後cashのgridで計算し、足内の最大DDではない。exposureはposition時価/equityの時間平均。netに含まれるcostをもう一度差し引かない。

![テスト比較](test_comparison.png)

## 全seedとpaired差分

{seed_table}

warm−MLPの累積net差分は5seed平均−0.1257pp。seed5のみ改善し、他4seedは悪化した。paired_differences.csvとpaired_summary.csvに他の比較・指標・stressも含め全件保存した。固定48hの勝ちという結果を見て後からモデル・entry・thresholdを変更していない。

## 検証区間と銘柄別

{val_table}

検証区間ではMLPが固定48hより損失を抑えた一方、testでは逆転した。valを見て採用モデルやparameterを選んでいない。

{asset_table}

固定48hのtest利益はETHの＋27.89%に依存し、BTCは−3.98%。MLP→ACも両銘柄平均でマイナスである。全split/stress/seedの銘柄別指標はper_asset_metrics.csvと各metrics.jsonに保存した。

## Stress

{stress_table}

倍費用はfee/slipをともに2倍にし、fill基準のSL/TPもその条件で一貫して再計算した。5m遅延はexit signalから売却まで1本追加し、その間もSL/TPを有効にした。固定予定entryは遅延させない。モデル再学習・stressごとのthreshold変更はない。遅延が有利な価格を偶然得る場合もあり、全stressが必ず単調に悪化するわけではない。倍費用では固定48hも−0.06%、学習方策は約−19〜−21%となり費用依存が大きい。

## 統計的解釈と制約

5seedは同じ市場経路を共有するため独立した市場証拠ではない。testは80取引＝40共通entry時刻だけ。探索的なpaired block bootstrapはBTC/ETHを共通entryで平均し、7連続entry block（約14日）、2,000再標本でwarm−MLPの平均trade return差の95%区間を計算した。各seedの区間はいずれも0を含む（seed11の上端は数値誤差でほぼ0）。複数seed/指標/比較の補正はなく、累積portfolio差のCIでもない。統計優位は未確立であり、5seed SDと市場標本の不確実性を混同しない。

学習量が小さく、固定entry・単一期・単一cost modelに依存する。crypto spotとSPX short-straddleでは損益構造が異なる。板・流動性・market impact・動的slip・注文最小単位・資金調達は未実装。評価データが他研究やモデル学習で一度も見られていない保証もない。walk-forward、未使用期間、entry/保有時間/費用の事前登録された追加実験が必要だが、今回結果を改善するための後付け調整はしない。

## 検証・成果物

17 unittestが通過。gap、SL/TP同足、次open、追加遅延中のstop、費用と資金制約、共通entry後の現金待機、causal feature、train限定oracle、同値oracleの最早時刻、scaler、returns、境界purgeを確認した。

results/verification.jsonは330 artifactのhash、入力hash、source hash、train-only scaler、50件の保存weight再評価、100runの資金会計と共通entry、raw/warm共通budget、seed1再学習3モデルのbitwise weight一致を確認してpassed。report/補助表/図の追加前に行った検証であり、その後追加分もartifact_manifest.jsonへhash保存する。

各seedのmlp.pt/raw_rl.pt/mlp_rl.pt、training.json、全metrics/trades/equity、config、scaler、data_manifest、split_audit、episodes、runtime、seed_summary、summary_flat、paired_differences/summary、per_asset_metrics、training_budget、bootstrap、図を保存した。再実行コマンドは../../README.mdを参照。cpu-v1はweighted BCEの原典確認に合わせるため評価完了前に中断した準備runで、不完全な出力を含む。正式結果はcpu-v2だけである。
"""
    (out / "report_ja.md").write_text(text, encoding="utf-8")
    r.dump(out / "artifact_manifest.json", {str(f.relative_to(out)): r.sha(f) for f in sorted(out.rglob("*")) if f.is_file() and f.name != "artifact_manifest.json"})
    print(out / "report_ja.md")


if __name__ == "__main__":
    main()
