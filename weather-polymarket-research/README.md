# Weather Polymarket Research

New Yorkの日最高気温を対象に、Polymarketの市場ルール、GEFS予報、公式観測、価格履歴をPoint-in-Time制約付きで研究するプロジェクトです。

このプロジェクトは注文を送信しません。履歴予報や観測が不足する場合は、合成データで補完せず `insufficient_data` として記録します。

## 開発環境

PowerShellで以下を実行します。

    .\.venv\Scripts\python.exe -m pip install -e ".[test]"
    .\.venv\Scripts\python.exe -m pytest -q

## CLI

.\.venv\Scripts\python.exe -m weather_research.cli --help

対象日のGEFSメンバーとCLOB価格を追加取得する場合:

    .\.venv\Scripts\python.exe -m weather_research.cli collect-gefs-target --target-date 2026-01-06 --issue-time 2026-01-05T12:00:00Z --output-dir data
    .\.venv\Scripts\python.exe -m weather_research.cli collect-gefs-market-days --output-dir data --issue-cycle-hour 12 --max-members 21 --max-workers 4 --timeout-seconds 120 --max-retries 5 --retry-backoff-seconds 2
    .\.venv\Scripts\python.exe -m weather_research.cli collect-prices-target --target-date 2026-01-06 --start-time 2026-01-05T12:00:00Z --end-time 2026-01-07T05:00:00Z --output-dir data

GEFS market-day collection is resumable. It groups duplicate buckets by target
date, stores raw index/message responses under `data/raw/gefs-cache/`, retries
transient HTTP failures, and checkpoints normalized members plus
`results/gefs_forecast_manifest.json` after each target day. Use `--max-days` for
a bounded smoke run; omit it for every date in `market_rules.jsonl`. The
manifest remains `in_progress` if the long-running process is interrupted, and
the next invocation requests only missing ensemble members.

Backtesting reads all four normalized JSONL artifacts and only emits trades when
forecast, observation, and price periods align. Level 1 accepts the historical
CLOB price as a documented mid/last-price proxy. Level 2 requires a historical
best ask and never substitutes that proxy for an ask.

    .\.venv\Scripts\python.exe -m weather_research.cli run-backtest --data-dir data --results-dir results --level both --report reports/backtest_report.md

収集・バックテストの詳細はリポジトリルートの `.agent/2026-08-09-weather-polymarket-research.md` を参照してください。
