from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
from PIL import Image

from .artifacts import write_json_atomic, write_text_atomic


def _native(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_table(results_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(results_dir / "tables" / name)


def _markdown_table(frame: pd.DataFrame, limit: int = 16) -> str:
    visible = frame.head(limit).copy()
    if visible.empty:
        return "_insufficient_data_"
    for column in visible.columns:
        visible[column] = visible[column].map(
            lambda value: f"{value:.6g}" if isinstance(value, (float, np.floating)) and np.isfinite(value) else value
        )
    header = "| " + " | ".join(map(str, visible.columns)) + " |"
    separator = "| " + " | ".join(["---"] * len(visible.columns)) + " |"
    rows = ["| " + " | ".join(str(value) for value in row) + " |" for row in visible.itertuples(index=False, name=None)]
    note = f"\n\n先頭 {limit} 行を表示。全行は対応するCSVを参照。" if len(frame) > limit else ""
    return "\n".join([header, separator, *rows]) + note


def build_report(
    root: Path,
    config: dict[str, object],
    quality: dict[str, object],
    descriptive: dict[str, object],
    regimes: dict[str, object],
    walk_forward: dict[str, object],
    figures: list[str],
    tables: list[str],
) -> Path:
    results_dir = root / "results"
    state_stats = _read_table(results_dir, "08_regime_state_statistics.csv")
    model_selection = _read_table(results_dir, "08_regime_model_selection.csv")
    walk_states = _read_table(results_dir, "09_walk_forward_state_outcomes.csv")
    strategy = _read_table(results_dir, "09_walk_forward_strategy_performance.csv")
    extremes = _read_table(results_dir, "01_extreme_candle_aftermath.csv")
    derivatives = _read_table(results_dir, "07_derivatives_events.csv")
    risk = _read_table(results_dir, "06_mdd_bootstrap.csv")
    barrier = _read_table(results_dir, "06_triple_barrier.csv")
    best_fee5 = strategy[strategy["fee_bps"] == 5].sort_values("sharpe", ascending=False).iloc[0]
    start = str(config["history_start"])
    detail_start = str(config["detail_start"])
    inclusive_end = (pd.Timestamp(str(config["end_date_exclusive"])) - pd.Timedelta(days=1)).date().isoformat()
    expected_metrics = (pd.Timestamp(str(config["end_date_exclusive"])) - pd.Timestamp(detail_start)).days * 24 * 12
    metrics_note = quality["input_manifest"]["metrics"]
    funding_note = quality["input_manifest"]["funding_rate"]
    report = rf"""# BTCUSDT USD-M先物: レジーム識別 EDA と walk-forward

生成日時: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

## 結論の要約

この分析は Binance USD-M perpetual futures の BTCUSDT を対象とする探索的分析です。1時間足・日足は {start} から {inclusive_end}、15分足・Funding・Metrics は {detail_start} から {inclusive_end} を使用しました。全標本HMM/GMMは記述目的のみであり、売買性能の根拠には使いません。

- 全標本の選択状態数: HMM **{regimes['selected_hmm_states']}**、GMM **{regimes['selected_gmm_states']}**。HMM seed ARI は **{regimes['hmm_seed_mean_ari']:.3f}**。
- Walk-forward は **{walk_forward['prediction_rows']:,} 時点**、将来参照違反 **{walk_forward['future_reference_violations']}**。状態数は初期学習窓で固定し、各月で過去データだけを再学習しました。
- 5 bps/ポジション変更の診断戦略で最高Sharpeは **{best_fee5['strategy']}** (total return {best_fee5['total_return']:.2%}, Sharpe {best_fee5['sharpe']:.2f}, MDD {best_fee5['max_drawdown']:.2%})。これは未約定・スリッページ・建玉上限を含まない研究用の比較であり、実運用の成績ではありません。
- Metrics は期待 {expected_metrics:,} 本に対して {quality['metrics']['rows']:,} 本です。公式ソース由来の欠損 {metrics_note['missing_intervals']} 区間は補間していません。Fundingは公開遅延のため {funding_note['last_time']} までで、範囲全体には不足があります。
- 清算はPublic Dataに履歴アーカイブがないため、OI減少・出来高z-score・下位3%リターンによる `liquidation_proxy` を明示的な代理指標として扱います。CMEの実価格ギャップも `insufficient_data` です。

## 入力データ品質

| dataset | rows | first | last | gaps/status |
| --- | ---: | --- | --- | --- |
| 15m Kline | {quality['15m']['rows']:,} | {quality['15m']['first']} | {quality['15m']['last']} | {quality['15m']['interval_gaps']} |
| 1h Kline | {quality['1h']['rows']:,} | {quality['1h']['first']} | {quality['1h']['last']} | {quality['1h']['interval_gaps']} |
| 1d Kline | {quality['1d']['rows']:,} | {quality['1d']['first']} | {quality['1d']['last']} | {quality['1d']['interval_gaps']} |
| Funding | {quality['funding']['rows']:,} | {quality['funding']['first']} | {quality['funding']['last']} | {funding_note['status']} |
| Metrics | {quality['metrics']['rows']:,} | {quality['metrics']['first']} | {quality['metrics']['last']} | {metrics_note['status']} |

全Klineの時刻は「足終値が利用可能になるUTC境界（close_time + 1ms）」です。ダウンロード済みアーカイブは公式CHECKSUMで検証し、正規化CSVのOHLC・時刻間隔・有限値も再検査しています。

## 分布・季節性・プライスアクション

![QQ plots](figures/01_return_qq.png)

15分足/1時間足/日足の分布と極端足後の条件付き挙動です。平均値と比率の95%区間は、時系列依存を残すブロック・ブートストラップで計算しています。

{_markdown_table(extremes)}

![ATR autocorrelation](figures/01_atr_autocorrelation.png)

![Seasonality](figures/02_seasonality_heatmap.png)

![Candle anatomy](figures/03_candle_anatomy.png)

## 出来高・マルチタイムフレーム・リスク

![Return and volume](figures/04_return_volume_scatter.png)

![Volume profile](figures/04_volume_profile.png)

![MTF context](figures/05_mtf_context.png)

日足の特徴量は、各15分足に対して完了済みの日足のみをas-of結合しています。従って、当日未確定の日足終値は下位足判定へ流入しません。

![Triple barrier](figures/06_triple_barrier.png)

`upper_probability_given_resolution` は、同一足で両バリアに触れた曖昧ケースとタイムアウトを除いた条件付き確率です。

{_markdown_table(barrier)}

![MDD bootstrap](figures/06_mdd_bootstrap.png)

SMA20/50の単純戦略をブロック・ブートストラップしたMDD推定では、中央値 {risk.iloc[0]['median_mdd']:.2%}、5%分位 {risk.iloc[0]['mdd_5pct']:.2%}、資産半減確率 {risk.iloc[0]['half_equity_probability']:.2%} です。

## Funding・OI・清算代理指標

![Derivatives events](figures/07_derivatives_events.png)

{_markdown_table(derivatives)}

## HMM / GMM / 変化点

{_markdown_table(model_selection)}

![Descriptive regimes](figures/08_descriptive_hmm_regimes.png)

{_markdown_table(state_stats)}

![PELT change points](figures/08_pelt_change_points.png)

全標本HMM/GMMおよびPELTは将来分布を含む説明用です。`bull_trend` / `bear_stress` の符号条件を満たさない状態は `momentum_proxy` / `stress_proxy` と明記しており、任意の経済的ラベルを後付けしていません。

## Walk-forward（売買シグナルではなく検証用）

初期窓でHMM/GMMの状態数を選び、以後は月初ごとに過去730日（欠損なしの暦スパンかつ有効行99%以上）だけで再学習しました。HMMの本番ラベルは逐次forward filter、GMMは当時点特徴量の `predict_proba` を用いています。月次のViterbi ARIは「追加学習を含む再fit後の状態分割」の安定性指標であって、オンライン推論の精度ではありません。

{_markdown_table(walk_states)}

![Walk-forward performance](figures/09_walk_forward_performance.png)

{_markdown_table(strategy)}

## 再現手順

```powershell
cd C:/Users/Hodaka/Downloads/div/bot_research/binance-btcusdt-futures-research
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe download_binance_klines.py --end-date {config['end_date_exclusive']}
.\.venv\Scripts\python.exe collect_research_inputs.py --config configs/research.json
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage all
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage verify
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage reproduce-check
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage report
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage verify
```

`results/manifest.json` は入力アーカイブ、正規化CSV、設定、成果物のSHA-256を保持します。GPUはこの規模では優位性がないため使わず、月次再学習はCPU 4ワーカー・ライブラリ内部1スレッドに制限しています。
"""
    report_path = results_dir / "report.md"
    write_text_atomic(report, report_path)
    versions = {package: importlib.metadata.version(package) for package in (
        "numpy", "pandas", "scipy", "matplotlib", "seaborn", "scikit-learn", "hmmlearn", "ruptures", "statsmodels", "pillow", "threadpoolctl"
    )}
    input_manifest_path = root / "metadata" / "research-inputs.json"
    fetch_manifest_path = root / "metadata" / f"fetch-{config['end_date_exclusive']}.json"
    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    fetch_manifest = json.loads(fetch_manifest_path.read_text(encoding="utf-8"))
    normalized_input_paths = [root / "data" / "BTCUSDT-15m-365d.csv", *[root / item["path"] for item in quality["input_manifest"].values()]]
    artifact_paths = [report_path, results_dir / "analysis-summary.json"]
    artifact_paths.extend(results_dir / "figures" / name for name in sorted(figures))
    artifact_paths.extend(results_dir / "tables" / name for name in sorted(tables))
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "config": config, "package_versions": versions,
        "quality": quality, "summary": {"descriptive": descriptive, "regimes": regimes, "walk_forward": walk_forward},
        "figures": sorted(figures), "tables": sorted(tables),
        "input_manifest_sha256": _sha256_path(input_manifest_path), "fetch_manifest_sha256": _sha256_path(fetch_manifest_path),
        "input_archives": input_manifest["archives"] + [{"dataset": "initial_365d_klines", "period": "mixed", "url": item["url"], "local_path": item["path"], "sha256": item["sha256"]} for item in fetch_manifest["archives"]],
        "normalized_input_sha256": {str(path.relative_to(root)).replace("\\", "/"): _sha256_path(path) for path in normalized_input_paths},
        "analysis_definitions": {"kline_time": "bar close availability boundary in UTC (close_time + 1 millisecond)", "extreme_candle": "bottom/top 3 percent of returns within each timeframe)", "liquidation_proxy": "bottom-3-percent return, OI decrease, and volume z-score above 3; not actual liquidation", "triple_barrier": "upper probability conditional on unambiguous resolution; timeouts excluded"},
        "artifact_sha256": {str(path.relative_to(results_dir)).replace("\\", "/"): _sha256_path(path) for path in artifact_paths},
        "config_sha256": hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    write_json_atomic(_native(manifest), results_dir / "manifest.json")
    return report_path


def verify_results(root: Path) -> dict[str, object]:
    results_dir = root / "results"
    manifest = json.loads((results_dir / "manifest.json").read_text(encoding="utf-8"))
    missing: list[str] = []; invalid_images: list[str] = []; invalid_tables: list[str] = []; hash_mismatches: list[str] = []
    for name in manifest["figures"]:
        path = results_dir / "figures" / name
        try:
            with Image.open(path) as image: image.verify()
        except Exception: invalid_images.append(name)
    for name in manifest["tables"]:
        path = results_dir / "tables" / name
        if not path.exists() or path.stat().st_size == 0:
            missing.append(str(path.relative_to(root))); continue
        try:
            frame = pd.read_csv(path); numeric = frame.select_dtypes(include="number")
            statuses = [column for column in frame if column == "status" or column.endswith("_status")]
            explained = pd.Series(False, index=frame.index)
            for column in statuses: explained |= frame[column].isin(("insufficient_data", "not_applicable_first_window"))
            if frame.empty or (numeric.size and np.isinf(numeric.to_numpy()).any()) or (frame.isna().any(axis=1) & ~explained).any(): invalid_tables.append(name)
        except Exception: invalid_tables.append(name)
    report_path = results_dir / "report.md"; report_text = report_path.read_text(encoding="utf-8")
    broken_links = [link for link in re.findall(r"!\[[^]]*\]\(([^)]+)\)", report_text) if not (results_dir / link).exists()]
    for relative, expected in manifest["artifact_sha256"].items():
        path = results_dir / relative
        if not path.exists(): missing.append(relative)
        elif _sha256_path(path) != expected: hash_mismatches.append(relative)
    for relative, expected in manifest["normalized_input_sha256"].items():
        path = root / relative
        if not path.exists(): missing.append(relative)
        elif _sha256_path(path) != expected: hash_mismatches.append(relative)
    for path, expected, label in ((root / "metadata" / "research-inputs.json", manifest["input_manifest_sha256"], "metadata/research-inputs.json"), (root / "metadata" / f"fetch-{manifest['config']['end_date_exclusive']}.json", manifest["fetch_manifest_sha256"], "fetch manifest")):
        if not path.exists() or _sha256_path(path) != expected: hash_mismatches.append(label)
    archive_missing: list[str] = []; archive_hash_mismatches: list[str] = []
    for archive in manifest["input_archives"]:
        path = root / archive["local_path"]
        if not path.exists(): archive_missing.append(archive["local_path"])
        elif _sha256_path(path) != archive["sha256"]: archive_hash_mismatches.append(archive["local_path"])
    provenance = pd.read_csv(results_dir / "tables" / "09_walk_forward_prediction_provenance.csv")
    time = pd.to_datetime(provenance["time"], utc=True); fit = pd.to_datetime(provenance["model_fit_end"], utc=True); available = pd.to_datetime(provenance["feature_available_at"], utc=True); prediction_end = pd.to_datetime(provenance["prediction_for_end"], utc=True)
    month_start = pd.DatetimeIndex([value.replace(day=1, hour=0, minute=0, second=0, microsecond=0) for value in available])
    provenance_violations: list[str] = []
    if time.duplicated().any() or not time.is_monotonic_increasing or not (time == available).all(): provenance_violations.append("time and feature availability are not a unique ordered match")
    if not (prediction_end == available + pd.Timedelta(hours=1)).all(): provenance_violations.append("prediction horizon is not exactly one hour")
    if not (fit == month_start - pd.Timedelta(hours=1)).all(): provenance_violations.append("model fit end is not exactly prior month end")
    expected_times = pd.date_range(time.min(), time.max(), freq="h")
    missing_prediction_times = expected_times.difference(pd.DatetimeIndex(time))
    if len(missing_prediction_times):
        from .data import load_inputs
        from .features import REGIME_FEATURES, build_hourly_features
        available_feature_times = build_hourly_features(load_inputs(root, manifest["config"])["1h"]).dropna(subset=REGIME_FEATURES).index
        unexpected = missing_prediction_times.intersection(available_feature_times)
        if len(unexpected): provenance_violations.append("prediction rows have unexplained hourly gaps")
    monthly = pd.read_csv(results_dir / "tables" / "09_walk_forward_monthly.csv")
    months = pd.to_datetime(monthly["month"], utc=True); train_start = pd.to_datetime(monthly["train_start"], utc=True); gate_end = pd.to_datetime(monthly["gate_label_end"], utc=True)
    required = int(manifest["config"]["walk_forward_train_days"]) * 24
    if not ((months - train_start) >= pd.Timedelta(days=int(manifest["config"]["walk_forward_train_days"]))).all(): provenance_violations.append("training calendar span is too short")
    if not (monthly["train_rows"] >= int(required * 0.99)).all(): provenance_violations.append("usable training rows below declared 99 percent threshold")
    if not (gate_end < months).all(): provenance_violations.append("gate labels reach test month")
    if not (monthly["converged"] & monthly["gmm_converged"] & (monthly["hmm_train_min_occupancy"] >= 0.05) & (monthly["gmm_train_min_occupancy"] >= 0.05)).all(): provenance_violations.append("monthly model quality gate failed")
    if monthly["test_rows"].sum() != len(provenance): provenance_violations.append("monthly and provenance row counts disagree")
    from .data import load_inputs, quality_summary
    live_quality = quality_summary(load_inputs(root, manifest["config"]), root)
    input_quality_mismatches = [f"{name}.{field}" for name in ("15m", "1h", "1d", "funding", "metrics") for field in ("rows", "first", "last", "duplicates") if live_quality[name][field] != manifest["quality"][name][field]]
    status = "success" if not any((missing, invalid_images, invalid_tables, broken_links, hash_mismatches, archive_missing, archive_hash_mismatches, provenance_violations, input_quality_mismatches)) else "failed"
    payload = {"status": status, "checked_at_utc": datetime.now(timezone.utc).isoformat(), "missing_artifacts": missing, "invalid_images": invalid_images, "invalid_tables": invalid_tables, "broken_report_links": broken_links, "artifact_hash_mismatches": hash_mismatches, "input_archive_missing": archive_missing, "input_archive_hash_mismatches": archive_hash_mismatches, "input_quality_mismatches": input_quality_mismatches, "provenance_violations": provenance_violations, "figure_count": len(manifest["figures"]), "table_count": len(manifest["tables"])}
    write_json_atomic(payload, results_dir / "verification.json")
    if status != "success": raise ValueError(f"result verification failed: {payload}")
    return payload
