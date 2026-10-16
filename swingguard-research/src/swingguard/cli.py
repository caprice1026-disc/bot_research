"""Read-only public downloads and offline experiments; no exchange executor."""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path
import platform
import tempfile

from .data import DAY_MS, canonical_hash, date_ms, download, load, sha, write_json
from .engine import run_engine
from .providers import GeminiProvider, PROMPT, RuleProvider, SCHEMA


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def estimate(config):
    counts = {name: (date_ms(bounds[1]) - date_ms(bounds[0])) // DAY_MS for name, bounds in config["periods"].items()}
    days = sum(counts.values())
    c = config["gemini"]
    upper = (Decimal(c["max_input_bytes"]) * Decimal(c["assumed_input_usd_per_million"]) +
             Decimal(c["max_output_tokens"]) * Decimal(c["assumed_output_usd_per_million"])) / 1_000_000
    return {"days_by_period": counts, "proposal_requests_three_arms": days * 3,
            "unique_gemini_requests_with_post_and_uncontrolled_cache_sharing": days * 2,
            "symbols_in_each_joint_request": config["symbols"],
            "distinct_model_inputs_per_day": ["pre_risk", "post_only"],
            "uncontrolled_request_reuses": "post_only (identical market features, no account/risk input)",
            "hard_budget_limit_usd": "10",
            "candidate_model": c["model"], "legacy_repo_model": c["legacy_repo_model"],
            "account_model_availability": "unverified_no_key",
            "official_candidates": ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"],
            "free_tier_status": "listed_in_public_docs_account_quota_unverified_no_smoke_sent",
            "pricing_status": c["pricing_status"], "assumed_input_usd_per_million": c["assumed_input_usd_per_million"],
            "assumed_output_usd_per_million": c["assumed_output_usd_per_million"],
            "max_input_byte_as_token_conservative_cost_usd": str(upper * days * 2),
            "reservation_envelope_usd": str(Decimal(c["reservation_usd_per_request"]) * days * 2),
            "reservation_note": "Sum of 730 hypothetical outstanding reservations, not prepaid fees. Sequential calls replace each 0.05 reservation with token-based settled cost. Settled plus unknown reservations plus next reservation must stay within the 10 USD budget.",
            "pricing_source": c["pricing_source"], "model_source": c["model_source"],
            "notes": "Public Standard pricing checked 2026-10-16; discounted rates expire 2026-12-31. Not an account quote. Output includes thinking; account/model/quota require validation. Unknown requests retain reservation; no retries."}


def report_text(summary, config):
    lines = ["# SwingGuard-inspired price-only adaptation", "", "日次提案と5分リスク制御のオフライン比較。原典の完全再現ではない。", "",
             "原典はGPT-4oとニュース/onchain/macro等を使用するが、本実験はBTC/ETHの価格特徴量と明示的なルールを使用する。数値閾値、stop/TP、復帰条件、offline ruleは適応の事前仮定であり、評価結果を見た最適化は行っていない。", "",
             "## 比較の意味", "",
             "pre_riskは当時の自口座のstate/DD/日次損失/市場下落を日次providerに渡し、生成後にも同一controllerを通す。post_onlyは生成前にriskを渡さず、生成後だけ制御する。uncontrolledは4状態制御を無効にするが、共通の固定SL/TPを維持する。buy_holdは初日に50/50の購入指示を出し、SL/TP・日次リバランスなしで期末に売却する。", "",
             "offline providerは通常時に20日平均と正の7日momentum、非NORMAL時に60日平均と1.5%超の7日momentumを要求する。60日平均への置換が常に厳しい条件となるわけではなく、riskに応じた別のtrend条件である。pre/postは自口座riskの閉ループ比較であり、同一risk系列の提示順だけを変えた実験ではない。LLMへの事前risk入力効果を立証しない。",
             f"このrunのprovider={summary['provider']}、API送信件数={summary['api_requests_sent_this_run']}。", "",
             "## データと執行", "",
             f"Binance現物公開5分OHLCV、{config['data_start']}〜{config['data_end_exclusive']}未満。先行60日をwarmupに使用。全月ZIPの配布SHA-256と正規化CSV hashを保存。ms/us、OHLC、重複、欠損、順序、境界、銘柄間一致を検証し、欠損補間はしない。", "",
             "日次UTC境界に直前まで確定した足だけで提案する。数量は直前終値と当時資産で固定し、次の利用可能な5分始値に片道10bp commission＋5bp slippageで約定。Gemini遅延は到着後最初の始値まで繰り越す。共同weightをmax(1,合計)で正規化し、買付資金不足は比例縮小する。現金・端数量のspot相当、空売り・借入・perp Funding・清算はない。", "",
             "共通SL8%、TP16%。gap stopは不利な始値、gap TPはTP価格、同足両到達はSL優先。intrabar時刻は不明。保護決済後はその後の正常な日次proposalまで再購入せず、待機中のstopを古い要求の応答で解除しない。期末は最終確定終値で全armに同じ売却費用を課す。", "",
             "v2では新規注文期限24hを維持し、日次更新中だけ旧建玉を最大15分保護する（最大staleness24h15m）。猶予中は買い増し/再購入禁止で、SL/TP/risk縮小は有効。新提案の許容遅延も15分、受信後最初のopenで切替。既知の不正応答は受信時flat、無応答/遅すぎる応答は固定deadlineでflat。課金結果不明等でproviderが停止したrunは中断し成功結果を公開しない。15分は運用猶予の事前仮定で、成績から選ばない。", "",
             "SL/TPの基準は初回fill価格。追加購入時はSLをmax(従来stop,新平均取得価格×0.92)として緩めず、TPは最初の建玉fill価格×1.16に固定する。したがって追加後の平均取得価格から16%上のTPとは限らない。部分売却でも価格アンカーを維持し、flat後の新規建玉で更新する。日次proposalの更新だけでは建玉の保有時間やSL/TPをリセットしない。固定の最大保有時間は設けず、見送り/縮小/stop/TP/risk/期末で決済する。", "",
             "## リスクの事前仮定", "",
             "NORMAL/ALERT/SURVIVAL/RECOVERYの配分上限は1/.5/0/段階.25,.5,.75,1。ALERTはepisode DD4%、当日損失2%、最大銘柄1h下落3%のいずれか。SURVIVALは12%/6%/8%で次openに全決済。最低12hの待機と静穏条件で復帰開始、静穏な24hごとに段階復帰する。復帰中3%再下落などは再停止。", "",
             "復帰完了時にcontrollerのepisode高値を更新するが、成績DDの全期間高値はリセットしない。したがって12%は全期間DDの保証ではない。API costを除く時価資産をcontrollerに使用する。", "",
             "v2では最新確定足をその足が属する日の基準で判定してから新日基準を更新する。00:00のdaily_loss/completed_day_lossは直前完了日、current_day_lossは新当日0。境界でcontrollerを二重更新せず、前日最後の5分足の損失を消さない。exposure drift許容0.5ポイントと次open補正のため、capは足内/連続時間の硬上限ではない。", "",
             "## 結果", "",
             "利益・費用はUSDT、DDは5分時価資産の全期間高値比、SharpeはUTC日次リターン・365日年率化・無リスク金利0。turnoverは片道約定額の絶対値合計/初期資産であり、往復でさらに倍しない。fill数とflat-to-flat完結保有数を分ける。exposureは5分時点の平均gross/equity。", ""]
    for period, item in summary["periods"].items():
        lines += [f"### {period}: {item['bounds'][0]}〜{item['bounds'][1]}未満", "",
                  "| arm | return % | max DD % | daily Sharpe | turnover x | fills | fee | slippage | exposure % |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for arm, m in item["arms"].items():
            sharpe = f"{m['daily_sharpe_365_rf0']:.3f}" if m['daily_sharpe_365_rf0'] is not None else "n/a"
            lines += [f"| {arm} | {m['return_pct']:.3f} | {m['max_drawdown_pct']:.3f} | {sharpe} | {m['turnover_over_initial']:.3f} | {m['fill_count']} | {m['commission']:.3f} | {m['slippage_cost']:.3f} | {m['mean_exposure_pct']:.2f} |"]
        lines += ["", f"pre/postの日次weightが異なる日数: {item['pre_post_weight_difference_days']}。各armの状態件数、絶対利益、保有完結数、ベンチマークとの差はsummary.jsonに記録した。", ""]
    e = summary["gemini_estimate"]
    lines += ["## Gemini と再実行", "",
              f"全{sum(e['days_by_period'].values())}日、3armで{e['proposal_requests_three_arms']}提案。1回でBTC/ETH両方のweightsを返す。post/uncontrolledの同一入力cacheを共有すれば、365日×2種の入力でGemini実リクエストは最大{e['unique_gemini_requests_with_post_and_uncontrolled_cache_sharing']}件（銘柄数2をさらに掛けない）。既存repoの3.5 Flashは非推奨で3.6へ自動転送されるため、新adapter候補を {e['candidate_model']} とした。キー・SDK・アカウントの利用可能モデルとquotaはreadiness記録で別途確認する。", "",
              f"2026-10-16確認の公開Standard料金 input ${e['assumed_input_usd_per_million']}/output ${e['assumed_output_usd_per_million']} per million （2026-12-31まで）を使い、入力上限10,000 bytesを同数tokenとして上限512 output tokensを足す保守的試算は ${e['max_input_byte_as_token_conservative_cost_usd']}、1件$0.05の予約枠合計は ${e['reservation_envelope_usd']}。無料枠は公開表にあるが、アカウントの利用可否・quotaは未確認である。", "",
              "$36.50は730件すべてが未確定の仮想的な予約額合計であり、前払い・予想請求額ではない。逐次実行では1件ごとの$0.05を実token×固定単価の確定費用へ置き換え、確定済み費用＋未確定予約＋次の予約が指定予算以内のときだけ送信する。指定上限は10 USD、SDK attemptは1回、結果不明・usage不足は停止して自動再送しない。同時writerはOS lockで拒否する。", "",
              "[公開料金](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.6-flash)、[3.5の非推奨と転送](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash)。公開候補は3.6 Flash、3.8 Flash、3.5 Flash-Lite。アカウントでのmodels.list照合はキー未確認のため未実施。", "",
              "v2 adapterはrequest/受信応答/usage/latency/単価/費用/検証済みproposalを同一journalレコードに保存し、受信保存後の中断は再送せず精算・cache再構築で復旧する。保存前の結果不明は予約を残して停止。totalと内訳を照合し、thinking欠落を0と確定できない場合は停止し、別hashも未確定課金を迂回できない。異常usageは予約以上の観測保守額を残す。単一attempt、構成モデル/単価/期限も照合する。", "",
              "費用ガードは同じjournalのローカル管理であり、別cache/他アプリ/価格変更/供給側のtoken制限違反を含む実invoiceの上限を保証しない。受信保存前の電源断、ディスク破損、未知のprovider不整合から無条件に自動復旧する保証もない。v1は保存し、修正前後の全arm差をresults/comparison_v1_v2に記録する。", "",
              "## 限界", "",
              "価格だけのルールによる探索的な適応で、LLMの成績ではない。後続半年はこの実験の閾値調整には未使用だが、既存repoの過去研究やモデル学習に未使用とは保証しない。将来Geminiを使う歴史検証ではlive retrievalを禁止しても、後年の値動きの記憶による汚染を排除できない。", "",
              "5分OHLCでは足内の到達順序、板・市場インパクト、正確なstop時刻、実際の流動性、取引最小単位、税・保管費を再現しない。固定費用は実取引所条件の保証ではない。Binance現物相当の結果をHyperliquidやperpの収益性と解釈しない。", "",
              "出典: https://arxiv.org/html/2610.12988v1 （日次判断/5分制御の構成は親タスクによる原典確認。非公開条件は本実験の仮定。）", ""]
    return "\n".join(lines)


def run_suite(config, data_root, output, provider_name, budget, cache_root=None):
    if output.exists():
        raise ValueError("output exists; use a new output directory")
    data, manifest = load(config, data_root)
    cache_root = cache_root or data_root / (provider_name + "-cache-v2")
    provider = (RuleProvider(config, cache_root) if provider_name == "rule" else
                GeminiProvider(config, cache_root, budget))
    source_root = Path(__file__).parent
    freeze = {"config": config, "config_hash": canonical_hash(config), "data_manifest": manifest,
              "python": platform.python_version(), "provider": provider_name,
              "prompt_hash": canonical_hash(PROMPT), "schema_hash": canonical_hash(SCHEMA),
              "source_sha256": {p.name: sha(p) for p in sorted(source_root.glob("*.py"))}}
    summary = {"label": config["label"], "provider": provider_name, "config_hash": freeze["config_hash"],
               "data_validation": "complete", "periods": {}, "gemini_estimate": estimate(config)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".run-") as temp:
        working = Path(temp)
        write_json(working / "freeze.json", freeze)
        for name, bounds in config["periods"].items():
            result_summary = {"bounds": bounds, "arms": {}, "state_bars": {}}
            proposals = {}
            for arm in ("pre_risk", "post_only", "uncontrolled", "buy_hold"):
                audit_start = len(provider.audit)
                result = run_engine(data, config, date_ms(bounds[0]), date_ms(bounds[1]), arm, provider)
                target = working / name / arm
                target.mkdir(parents=True)
                write_csv(target / "equity.csv", result["equity"])
                write_csv(target / "trades.csv", result["trades"])
                write_jsonl(target / "decisions.jsonl", result["decisions"])
                write_jsonl(target / "proposal_audit.jsonl", provider.audit[audit_start:])
                write_json(target / "risk_transitions.json", result["transitions"])
                result_summary["arms"][arm] = result["metrics"]
                result_summary["state_bars"][arm] = result["state_bars"]
                proposals[arm] = {d["as_of_ms"]: d["weights"] for d in result["decisions"] if "weights" in d}
                print(f"{name}/{arm}: return={result['metrics']['return_pct']:.3f}% DD={result['metrics']['max_drawdown_pct']:.3f}%", flush=True)
            result_summary["pre_post_weight_difference_days"] = sum(
                weights != proposals["post_only"].get(t) for t, weights in proposals["pre_risk"].items())
            benchmark = result_summary["arms"]["buy_hold"]["return_pct"]
            for m in result_summary["arms"].values():
                m["return_minus_buy_hold_percentage_points"] = m["return_pct"] - benchmark
            summary["periods"][name] = result_summary
        summary["api_requests_sent_this_run"] = provider.sent_count
        write_json(working / "summary.json", summary)
        (working / "report_ja.md").write_text(report_text(summary, config), encoding="utf-8")
        write_json(working / "gemini_estimate.json", estimate(config))
        artifacts = {p.relative_to(working).as_posix(): sha(p) for p in sorted(working.rglob("*")) if p.is_file()}
        write_json(working / "artifact_manifest.json", artifacts)
        working.rename(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "run", "estimate"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provider", choices=["rule", "gemini"], default="rule")
    parser.add_argument("--api-budget", type=Decimal, default=Decimal(0))
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    root = args.config.resolve().parent.parent
    data_root = args.data_root or root / "data"
    if args.command == "download":
        print(json.dumps(download(config, data_root)["datasets"], indent=2))
    elif args.command == "estimate":
        print(json.dumps(estimate(config), indent=2))
    else:
        run_suite(config, data_root, args.output or root / "results" / (args.provider + "-v2"), args.provider, args.api_budget, args.cache_root)


if __name__ == "__main__":
    main()
