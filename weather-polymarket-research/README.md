# Weather Polymarket Research

New Yorkの日最高気温を対象に、Polymarketの市場ルール、GEFS予報、公式観測、価格履歴をPoint-in-Time制約付きで研究するプロジェクトです。

このプロジェクトは注文を送信しません。履歴予報や観測が不足する場合は、合成データで補完せず `insufficient_data` として記録します。

## 開発環境

PowerShellで以下を実行します。

    .\.venv\Scripts\python.exe -m pip install -e ".[test]"
    .\.venv\Scripts\python.exe -m pytest -q

## CLI

    .\.venv\Scripts\python.exe -m weather_research.cli --help

収集・バックテストの詳細はリポジトリルートの `.agent/2026-08-09-weather-polymarket-research.md` を参照してください。
