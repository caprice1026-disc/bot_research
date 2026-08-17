# BTCUSDT USD-M Futures Research

Binance Public Dataを用いるBTCUSDT USD-M perpetual futuresの再現可能なEDA・HMM/GMMレジーム分析・月次walk-forward検証です。生データ、正規化CSV、実行時メタデータは容量と再取得可能性のためGit管理外です。分析結果・レポート・実行コードはGit管理します。

## 対象範囲

- 15分足: 2025-08-17 から 2026-08-16（365日）
- 1時間足・日足: 2020-01-01 から 2026-08-16
- Funding Rate と Binance Metrics: 15分足と同じ期間（公開遅延・公式欠損は補間しない）
- 時刻の意味: 各Klineの終値が利用可能になるUTC境界 (`close_time + 1ms`)

## 再現手順

```powershell
cd .\binance-btcusdt-futures-research
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe download_binance_klines.py --end-date 2026-08-17
.\.venv\Scripts\python.exe download_binance_klines.py --end-date 2026-08-17 --verify-only
.\.venv\Scripts\python.exe collect_research_inputs.py --config configs/research.json
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage all
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage verify
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage reproduce-check
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage report
.\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage verify
```

`--stage all` は前回の要約を再利用せず、同じ設定で成果物一覧を再構築します。月次fitはCPU 4ワーカー、各ライブラリの内部スレッドは1に制限します。GPUはこのデータ規模・利用ライブラリでは優位性がないため使用しません。

## 出力

- `results/report.md`: 方法・限界・主要テーブルを含むレポート
- `results/tables/`: CSVの集計結果
- `results/figures/`: PNG図表
- `results/manifest.json`: 入力・設定・成果物のSHA-256
- `results/verification.json`: 画像・CSV・入力ハッシュ・直接将来参照監査

Fundingの遅延、Metricsの公式欠損、清算・CMEギャップ価格の不在は、埋めずに`insufficient_data`または明示的な代理指標として記録します。結果は探索的研究であり、売買推奨や実運用の成績ではありません。
