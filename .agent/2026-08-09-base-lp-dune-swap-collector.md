# Base LP Dune Swap Collector ExecPlan

## 目的

Base WETH/USDC 0.05% pool の履歴取得をPublic RPCのブロック走査からDune raw logsへ移し、Dune API keyのデータポイント上限内で2026-06-01から2026-06-08までのSwapイベントを、既存の正規化・検証・Parquet・バックテストへ再現可能に供給する。

## 方針

- Duneの任意SQL APIを主データソースにする。`base.logs` をpool address、UTC期間、Swap topicで先に絞る。
- API keyは`DUNE_API_KEY`からのみ読み、値をログ・manifest・Gitへ残さない。リポジトリ直下の`.env`は実行時補助として安全に読み込む。
- API実行は一期間につき1回だけ行う。実行IDを保存し、状態は低頻度ポーリングする。結果はページングで順次保存し、同じSQLを繰り返さない。
- Duneが返す`block_time`、`block_number`、`block_hash`、topics、data、tx/hash/indexを`LogRecord`へ変換し、既存decoderをそのまま使う。
- 長期RPC workerは起動しない。既存`collect`は明示的なRPC検証用途として残し、bulk collectionの自動fallbackにはしない。

## 変更範囲

- 追加: `base-lp-research/src/base_lp/data/dune.py`
  - `DuneClient`、実行・状態待機・結果ページング、Dune rowから`LogRecord`への変換、SQL構築を提供する。
- 変更: `base-lp-research/src/base_lp/cli.py`
  - `collect-dune`を追加する。partial時のRPC collector全ファイルread/checksumを避けるため、Dune pathは1回のraw書込み後に完了時だけchecksumを算出する。
- 変更: `base-lp-research/src/base_lp/config.py`、`configs/base_weth_usdc_005.yaml`
  - sourceとDune key環境変数を設定として読み込む。
- 追加: `base-lp-research/tests/unit/test_dune.py`、必要なCLIテスト。
  - SQLがSwapと期間で絞られること、ページング順序、失敗状態、row変換、manifest provenanceを固定する。
- 変更: `base-lp-research/README.md`
  - Duneを主収集経路、上限内の7日実行コマンド、RPCの位置付けを記載する。

## 実装手順

1. Dune rowとHTTP応答を模したunit testを追加し、未実装で失敗することを確認する。
2. `DuneClient`を最小実装する。性能tierは送信せず、`execute -> status -> results?offset&limit`だけを扱う。APIエラー、timeout、failed/cancelled stateは明示的な例外にする。
3. Dune rowを`LogRecord`に変換し、`block_time`をUTC epoch secondsへ変換する。`topic0`から末尾のnull topicまでを順序保持する。
4. `collect-dune`を実装する。指定期間のSwapのみを一度実行し、raw JSONL、normalized Parquet、validation report、manifestを生成する。manifest metadataにsource、execution ID、SQL SHA-256、期間、全row数を記録する。
5. 既存全test、compileall、CLI helpを実行する。
6. `DUNE_API_KEY`を秘密値として出力せず`.env`からプロセスに投入して、7日の実収集を実行する。Dune失敗ならraw datasetを成功扱いせず、エラーを報告する。
7. 成功したデータだけでvalidate/backtest/sweepを実行し、`results/report.md`を更新する。結論は実データ期間とC0/P0等の制約を明示する。
8. diff・生成物除外・対象ファイルを確認し、関連ファイルのみcommitする。リモートpushは別途ユーザーの明示承認がある場合だけ行う。

## 検証条件

- `collect-dune --help`が表示される。
- Dune SQLはpool、`block_time >= start`、`block_time < end`、Swap topicで絞られる。
- ページング後の`LogRecord`はstable key順・重複なしで、既存`normalize_event_rows`がSwapとしてdecodeできる。
- manifestのsourceは`dune-sql`、errorsなし、execution IDとSQL hashを持つ。
- 取得範囲の全結果が成功時のみParquetとbacktestに渡る。partial/failed Dune executionを成功データと混同しない。

## 実行結果

- 2026-08-09: `DUNE_API_KEY`による認証と最小queryを確認した。明示performance tierは契約対象外のため送信しない。
- 2026-08-09: 1か月query（2026-06-01〜07-01）は759,888 Swap rowsを返したが、160,000行取得後にconfigured datapoint limitでHTTP 402になった。
- 2026-08-09: 結果取得を9列へ縮小し、7日query（2026-06-01〜06-08）を実行した。しかし当月上限がすでに尽きており最初の結果ページもHTTP 402となった。実行ID `01KZJDXXQWZ98Z7B5K535TKAPH`をcheckpointに保存し、`collection_error`として停止した。
- 2026-08-09: `collect-dune`はHTTP 402などを`collection_error`としてmanifestへ保存し、同一実行IDを再利用して新規SQLを発行しない。Dune上限の増額またはreset後に再開する。
