# Base Uniswap v3 LP Research v0.1

Base の WETH/USDC 0.05% Uniswap v3 pool を対象にした、AI/RL を使わない再現可能な研究基盤です。最初のバージョンでは、Swap イベントを時系列順に replay し、C0 counterfactual と P0 fee approximation で Static Narrow / Threshold Reset を比較します。

## 目的と制約

- 主指標は gross APR ではなく、HODL 相対の terminal net alpha、最大ドローダウン、time in range、rebalance 回数です。
- コストモデルには L2 execution、L1 security、inventory swap、price impact、slippage/MEV proxy の項目を持たせています。v0.1 の実行では receipt 由来の履歴値がない項目はゼロまたは明示的な proxy です。
- 実LPの所有者情報や個別 position の feeGrowth は使わず、少額 counterfactual LP として扱います。したがって実運用損益や裁定可能性の証明ではありません。
- 9か月未満のイベント窓は、数値を計算できても `research_status=insufficient_data` とします。

Base の手数料は L2 execution fee と L1 security fee の二つに分かれるため、長期分析では transaction receipt の履歴コストを別途検証します。[Base network fees](https://docs.base.org/base-chain/network-information/network-fees)

## セットアップ

PowerShell で以下を実行します。

```powershell
cd C:\Users\Hodaka\Downloads\div\bot_research\base-lp-research
& .venv\Scripts\python.exe -m pip install -e ".[test]"
```

RPC URL は環境変数から読み込みます。Base の公式接続先は rate limit があるため、取得範囲を小さく分割して再開してください。[Base connection](https://docs.base.org/base-chain/quickstart/connecting-to-base)

```powershell
$env:BASE_RPC_URL = "https://mainnet.base.org"
& .venv\Scripts\python.exe -m base_lp.cli resolve-pool --config configs\base_weth_usdc_005.yaml
& .venv\Scripts\python.exe -m base_lp.cli collect --config configs\base_weth_usdc_005.yaml --start-utc "2026-07-31T00:00:00Z" --end-utc "2026-07-31T01:00:00Z"
& .venv\Scripts\python.exe -m base_lp.cli validate-data --config configs\base_weth_usdc_005.yaml
& .venv\Scripts\python.exe -m base_lp.cli backtest --config configs\base_weth_usdc_005.yaml
& .venv\Scripts\python.exe -m base_lp.cli sweep --config configs\base_weth_usdc_005.yaml
```

`collect` はデフォルトで500 blockずつ取得し、RPC request間隔を0.25秒、1回の実行時間を90秒に制限します。`results/collection_checkpoint.json` と `data/raw/.../logs.jsonl` に進捗を保存するため、タイムアウトや中断後に同じコマンドを再実行すると完了済みchunkを再取得せず続きから再開します。`--fresh` を指定した場合だけ対象範囲を最初から取り直します。

必要に応じて、`--chunk-size`、`--request-interval-seconds`、`--rpc-timeout-seconds`、`--rpc-max-retries`、`--max-seconds` で取得負荷と1回の実行時間を調整できます。長期範囲では `--max-seconds 90` のまま繰り返し実行してください。

Uniswap v3 pool address は設定に固定せず、Factory の `getPool(tokenA, tokenB, fee)` から解決します。[Uniswap v3 deployments](https://developers.uniswap.org/docs/protocols/v3/deployments)

## 成果物

- `results/pool_metadata.json`: 解決した pool、token decimals、tick spacing
- `results/dataset_manifest.json`: block 範囲、行数、checksum、データ状態
- `results/validation_report.json`: stable key の重複・順序検証
- `results/backtest_summary.json`: 単一設定の backtest 結果
- `results/experiment_grid.json` / `results/experiment_grid.csv`: 3幅 × 3閾値の parameter sweep
- `results/report.md`: 人間が読むための要約
- `data/raw/` と `data/normalized/`: ローカル実行時のデータ。サイズが大きいため Git 管理対象外

## 現在の実データ smoke

2026-07-31 の1時間だけを公式 Base RPC から取得した smoke では、575 logs、511 Swap events、dataset validation 成功、C0 backtest 成功を確認しました。backtest は terminal value `4963.47 USD`、HODL alpha `-33.16 USD`、fees `5.52 USD`、rebalances `0` でした。

ただし1時間窓なので、これはパイプライン接続・decode・Parquet・replay の確認であり、投資判断に使える研究結論ではありません。長期 pilot は `research_status=insufficient_data` として扱います。RPC provider の rate limit、`eth_getLogs` の範囲制限、receipt コスト履歴、walk-forward の十分なサンプルが解決するまで結論を出しません。

## 検証

```powershell
& .venv\Scripts\python.exe -m pytest -q
& .venv\Scripts\python.exe -m compileall -q src tests
& .venv\Scripts\python.exe -m base_lp.cli --help
```

この研究コードは金融助言、運用 bot、実取引 executor ではありません。
