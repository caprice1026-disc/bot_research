# Polymarket天気市場 New York 研究基盤 ExecPlan

このExecPlanは、リポジトリ直下の `PLANS.md` に従う生きた実行計画である。`Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` は、作業の停止時点ごとに更新する。

## Purpose / Big Picture

この変更によって、`weather-polymarket-research/` でNew Yorkの日最高気温市場を再現可能に調査できるようにする。利用者は、市場ルール、GEFS予報、METARまたは同等の観測、Polymarket価格を取得し、取引時点より後に判明したデータを除外した上で、GEFSのバケット確率とLevel 1–2のバックテスト結果を確認できる。

研究の成功は、利益が出ることではなく、実データのカバレッジと限界を含む検証可能な結果を出せることである。履歴予報が取得できない場合は、合成データや後日修正された予報で補わず、`status=insufficient_data` と理由を成果物に残す。注文送信、APIキー利用、実資金の移動はこの計画の対象外である。

## Progress

- [x] (2026-08-09) リポジトリが初期コミットのみであること、`PLANS.md` のExecPlan規約、作業ディレクトリを確認した。
- [x] (2026-08-09) 研究文書を読み、初回範囲をNew York・GEFS/NWS/METAR・Polymarket・ベースライン・Level 1–2バックテストに限定した。
- [x] (2026-08-09) `docs/superpowers/specs/2026-08-09-weather-polymarket-research-design.md` を作成し、設計コミット `7f2a30f` を作成した。
- [x] (2026-08-09) Polymarket公式API、NOMADS GEFS、NCEI METARの候補エンドポイントを確認した。
- [ ] 研究ディレクトリのPythonパッケージ、依存関係、取得設定を作成する。
- [ ] Point-in-Timeスキーマと時点リーク除外をテスト先行で実装する。
- [ ] 市場ルール、GEFS確率、実行価格、バックテスト指標をテスト先行で実装する。
- [ ] Polymarket、GEFS、NCEI観測の再実行可能な収集器とマニフェストを実装する。
- [ ] 実データを取得し、結果を検証してレポートする。取得不能なデータは `insufficient_data` として記録する。
- [ ] 全テスト、結果検証、Git差分を確認し、最終成果物と残課題を報告する。

## Surprises & Discoveries

- Observation: ルートリポジトリにはREADME、PLANS、AGENTS、LICENSEしかなく、既存のアプリケーション構成やテスト基盤はない。
  Evidence: `git log --oneline` は初期コミット1件、`rg --files` は4つのルートファイルのみを返した。
- Observation: ローカル知識キャッシュの標準保存先はスキルディレクトリへの書き込み権限で失敗した。
  Evidence: `search_cache.py --no-web-fallback` が `PermissionError: [WinError 5]` で終了した。研究成果ではなく作業環境の制約として扱い、外部調査の根拠URLを仕様書に記録した。
- Observation: Polymarketは市場メタデータとCLOB価格データを別のAPI群で提供している。
  Evidence: 公式ドキュメントはGamma APIを市場・イベント用、CLOB APIを注文板・価格履歴用として案内している。
- Observation: 公開の天気Bot実装はOpen-Meteoの31メンバーGFSを現在の確率計算に使うが、後知恵なしの履歴予報アーカイブとは別問題である。
  Evidence: 参照READMEは31-member GFS ensembleを説明する一方、この研究では `forecast_issue_time` と `received_time` を必須にして履歴データの利用可否を別途検証する。

## Decision Log

- Decision: 初回都市はNew Yorkに固定する。
  Rationale: 都市を増やす前に、観測所、日界、決済ルール、予報格子点の対応を一つの市場で検証できるため。
  Date/Author: 2026-08-09 / Codex
- Decision: まずWeather Onlyモデルを主結果にし、市場価格を特徴量にするモデルを分離する。
  Rationale: 市場価格を再現しただけの性能と、気象情報からの追加情報を区別するため。
  Date/Author: 2026-08-09 / Codex
- Decision: 履歴予報がPoint-in-Timeで再構成できない期間は、合成データで補完せず `insufficient_data` とする。
  Rationale: 未来情報リークを防ぎ、ゼロ損益を成功または失敗のトレード結果と誤解しないため。
  Date/Author: 2026-08-09 / Codex
- Decision: 初回バックテストはLevel 1とLevel 2までとし、完全なL2再生は後続に分ける。
  Rationale: 価格履歴と板の再生可能性を混同せず、最小の再現可能な成果物を先に完成させるため。
  Date/Author: 2026-08-09 / Codex
- Decision: Python標準ライブラリをコアの正規化・計算に使い、外部依存はHTTP取得、数値計算、テスト、任意のParquet読込に限定する。
  Rationale: 取得環境が制限されても、ルール検証と小規模バックテストを実行できるようにするため。
  Date/Author: 2026-08-09 / Codex

## Outcomes & Retrospective

初期状態では研究コードとデータは存在しない。設計仕様書と本ExecPlanを先に用意し、実装は以下の受け入れ条件を満たした時点で評価する。実データが取れた場合は対象期間・件数・指標を報告し、取れなかった場合はデータ源ごとのHTTPエラー、空応答、時点整合不能を残す。利益の有無だけを理由にGo/No-Goを決めない。

## Context and Orientation

作業ディレクトリは `C:\Users\Hodaka\Downloads\div\bot_research` である。研究コードは既存ルートのファイルを変更せず、すべて `weather-polymarket-research/` に置く。ExecPlanは `.agent/2026-08-09-weather-polymarket-research.md`、設計仕様書は `docs/superpowers/specs/2026-08-09-weather-polymarket-research-design.md` にある。

`Point-in-Time` とは、ある取引時刻に本当に利用できた情報だけを使うという意味である。予報の発行時刻、受信時刻、予報対象時刻、取引時刻、決済時刻を別フィールドとして保存し、`received_time <= trade_time` を満たさないデータは使わない。

`MarketRule` は、市場説明を「都市・観測所・対象日・タイムゾーン・最高気温・バケット境界・決済ソース」に分解した構造体である。`invalid_market_rule` はルールが曖昧で結果を安全に決められない状態、`insufficient_data` はルールは有効だが必要なデータが不足する状態、`collection_error` は取得処理がエラーになった状態を表す。

## Plan of Work

### Milestone 1: 研究ディレクトリと実行可能な最小骨格

`weather-polymarket-research/pyproject.toml`、`README.md`、`src/weather_research/__init__.py`、`src/weather_research/cli.py`、`tests/`、`data/raw/`、`data/normalized/`、`results/`、`reports/`、`docs/` を作る。Python 3.11以上を対象にし、依存関係は `httpx`、`pydantic`、`numpy`、`pytest` を必須とし、Parquetの選択的読込が必要になった時だけ `duckdb` と `pyarrow` を追加する。最初のCLIは `python -m weather_research.cli --help` が終了コード0で動くことを確認する。

### Milestone 2: スキーマとPoint-in-Time検証

`src/weather_research/schemas.py` に `OutcomeStatus`、`MarketRule`、`ForecastMember`、`Observation`、`PricePoint`、`Signal`、`BacktestSummary` を定義する。`src/weather_research/pit.py` に `is_available_at_trade_time(record, trade_time)` と `filter_available_records(records, trade_time)` を実装する。予報発行時刻、受信時刻、市場価格時刻のいずれかが取引時刻より後なら、理由付きで除外する。

### Milestone 3: バケット確率と実行コスト

`src/weather_research/probability.py` に閉区間・半開区間を明示した `ensemble_bucket_probability(values, bucket)`、`gaussian_bucket_probability(mean, standard_deviation, bucket)`、`normalize_distribution(probabilities)` を定義する。`src/weather_research/execution.py` に `executable_yes_price(observed_ask, spread, slippage)`、`net_edge(model_probability, observed_ask, fee, spread, slippage, uncertainty_buffer)` を定義する。初期設定はspread 0.01、slippage 0.01、uncertainty buffer 0.02、minimum edge 0.08とし、計算に使った値をシグナルへ保存する。

### Milestone 4: 市場ルールと収集器

`src/weather_research/rules.py` でGamma APIの市場JSONから天気市場候補を抽出し、説明文とルールを検証する。自動抽出できない境界は推測せず、`invalid_market_rule` の検証結果へ送る。`src/weather_research/collectors/polymarket.py` はGamma APIのページングとCLOB価格履歴を担当する。`src/weather_research/collectors/noaa.py` はNew Yorkの決済観測所メタデータと観測を担当する。`src/weather_research/collectors/gefs.py` はNOMADS等の候補を調査し、予報発行時刻を保持できる形式だけを採用する。すべての収集器は生レスポンス、URL、取得UTC時刻、HTTPステータス、応答ハッシュ、エラーをマニフェストへ渡す。

### Milestone 5: 正規化、バックテスト、レポート

`src/weather_research/normalize.py` でUTCとAmerica/New_Yorkを変換し、観測所・対象日・市場バケットを結合する。`src/weather_research/backtest.py` は取引時刻ごとにPoint-in-Timeフィルタを適用し、モデル確率、ask、実行価格、結果、Gross/Net PnLを保存する。`src/weather_research/metrics.py` はBrier、Log Loss、ROI、Win Rate、平均/中央値エッジ、ドローダウン、最大連敗を計算する。`src/weather_research/reporting.py` は `results/dataset_manifest.json`、`results/backtest_summary.json`、`results/trades.csv`、`reports/backtest_report.md` を生成する。

### Milestone 6: 実データ検証と研究報告

CLIに `source-audit`、`collect`、`run-backtest`、`validate-results` を実装する。まず `source-audit` で市場、CLOB、GEFS、NCEIの利用可能性を確認し、次に実データ収集を行う。GEFS履歴が不足する場合は市場・観測の取得物を残したままバックテスト結果を `insufficient_data` とする。最後に全テスト、結果検証、生成ファイルのJSON/CSV整合性を実行し、使えたデータ、使えなかったデータ、研究上の結論を分けて報告する。

## Concrete Steps

### Task 1: 骨格とCLI

作業場所は `C:\Users\Hodaka\Downloads\div\bot_research` とする。次の順に実行する。

    New-Item -ItemType Directory -Force weather-polymarket-research, weather-polymarket-research\src\weather_research, weather-polymarket-research\tests, weather-polymarket-research\data\raw, weather-polymarket-research\data\normalized, weather-polymarket-research\results, weather-polymarket-research\reports, weather-polymarket-research\docs | Out-Null
    Set-Location weather-polymarket-research
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -e ".[test]"

`tests/test_cli.py` に `--help` のテストを先に書いて失敗を確認し、最小のargparse CLIを実装してから同じテストを通す。テスト用の環境構築だけは設定変更として扱い、動作コードは必ず次のタスクからテスト先行にする。

### Task 2: Point-in-TimeのRED-GREEN

先に `tests/test_point_in_time.py` へ、受信時刻が取引時刻より後の予報を除外するテスト、発行時刻は早いが受信時刻が遅い予報を除外するテスト、同時刻を許可するテストを書く。実行は `\.venv\Scripts\python.exe -m pytest tests/test_point_in_time.py -q` とし、関数未定義による失敗を確認する。次に `src/weather_research/schemas.py` と `src/weather_research/pit.py` を実装し、同じテストが通ることを確認する。

### Task 3: 確率とコストのRED-GREEN

先に `tests/test_probability.py` と `tests/test_execution.py` に、3/4メンバーがバケットに入る確率0.75、境界値の扱い、正規分布区間確率、確率分布の合計1、ask 0.40へのspread 0.01とslippage 0.01の適用、feeとuncertainty bufferを引いたnet edgeのテストを書く。失敗を確認してから `probability.py` と `execution.py` を実装する。`pytest tests/test_probability.py tests/test_execution.py -q` が全件成功し、NaN、負価格、1超の価格、標準偏差0を明示的に拒否することを受け入れ条件にする。

### Task 4: ルールと収集器のRED-GREEN

`tests/fixtures/` に、Gamma市場JSON、CLOB価格履歴JSON、NCEI観測JSON、GEFSメンバーの最小fixtureを置く。先に `tests/test_rules.py` と各collectorテストを書き、正常なmarket rule、曖昧な境界、HTTPエラー、空レスポンス、ページング、UTC時刻の保持を検証する。HTTP通信はfixtureを返す決定的なtransportを使い、ネットワークそのものの可用性をテストにしない。失敗を確認してから `rules.py` と `collectors/` を実装する。実取得のレスポンスはfixtureと同じスキーマへ正規化できなければ保存だけして利用対象から外す。

### Task 5: 正規化とバックテストのRED-GREEN

先に `tests/test_backtest.py` と `tests/test_metrics.py` を作り、同じ市場の予報が取引時刻後に届いた場合にシグナルが0件になること、履歴結果がYES/NOを正しくPnLへ変換すること、Level 1とLevel 2で実行価格が異なること、空データが `insufficient_data` になること、Brierとドローダウンが再現可能なことを固定する。失敗を確認してから `normalize.py`、`backtest.py`、`metrics.py`、`reporting.py` を実装する。

### Task 6: source-auditと実データ実行

テストを通した後、次のコマンドを研究ディレクトリで実行する。

    .\.venv\Scripts\python.exe -m weather_research.cli source-audit --city new-york --station KNYC --output results/source_audit.json
    .\.venv\Scripts\python.exe -m weather_research.cli collect --city new-york --station KNYC --output-dir data
    .\.venv\Scripts\python.exe -m weather_research.cli run-backtest --data-dir data --results-dir results --report reports/backtest_report.md
    .\.venv\Scripts\python.exe -m weather_research.cli validate-results --results-dir results

`source-audit` は各ソースについてURL、HTTPステータス、取得時刻、観測件数または予報件数、エラーを保存する。`collect` は同じ対象・同じ取得日で再実行しても既存のrawファイルを上書きせず、ファイルハッシュが同じなら再利用する。`run-backtest` は必要なデータが揃わない場合に終了コードを成功としても、JSONの `status` を `insufficient_data` にする。`validate-results` はJSONスキーマ、時点制約、PnL合計、CSV列、statusとreasonの整合性を検証し、`valid: true` を出す。

## Validation and Acceptance

実装中は対象テストを小さく実行し、最後に研究ディレクトリで `\.venv\Scripts\python.exe -m pytest -q` を実行する。全テストが成功し、出力に失敗やERRORがないことを確認する。続けて `validate-results` を実行し、`valid: true` を確認する。実データが十分な場合は `backtest_summary.json` の対象件数、期間、取引件数、Gross/Net PnL、最大ドローダウンがレポートと一致することを確認する。十分でない場合は、`status=insufficient_data`、原因、各ソースの取得件数、除外件数が一致することを確認する。

必要なデータが存在しない状態で `trades.csv` が空でも、それを「損益0で成功」と報告してはならない。市場ルールが不明なら `invalid_market_rule`、HTTPやファイル取得が失敗したなら `collection_error`、データは取れたがPoint-in-Time予報や観測が不足するなら `insufficient_data` として検証する。合成fixtureはテストにのみ使い、研究レポートの数値へ混ぜない。

## Idempotence and Recovery

収集器はrawファイルを追記または内容ハッシュで再利用し、同じURLの再取得で既存の正規化結果を破壊しない。途中で失敗した場合は、`data/raw/` の完了済みファイルとmanifestを残し、失敗したsourceだけ再試行する。外部サービスのレート制限や5xxには指数バックオフを最大3回まで使うが、再試行してもデータが取れない場合は `collection_error` にする。削除やリセットを行わず、必要な再計算は新しいresultsディレクトリへ出力する。

## Artifacts and Notes

最終的な最小成果物は次のとおりである。

    weather-polymarket-research/data/raw/
    weather-polymarket-research/data/normalized/market_rules.jsonl
    weather-polymarket-research/data/normalized/forecast_members.jsonl
    weather-polymarket-research/data/normalized/observations.jsonl
    weather-polymarket-research/data/normalized/price_points.jsonl
    weather-polymarket-research/results/source_audit.json
    weather-polymarket-research/results/dataset_manifest.json
    weather-polymarket-research/results/backtest_summary.json
    weather-polymarket-research/results/trades.csv
    weather-polymarket-research/reports/backtest_report.md

レポートには、対象市場のURLとID、決済観測所、対象期間、取得時点、除外ルール、各モデルの評価、Level 1/2の前提、データ不足、研究上の結論と未検証の仮説を分けて記載する。

## Interfaces and Dependencies

`src/weather_research/schemas.py` では、すべての時刻をtimezone-awareな `datetime` とし、価格・確率は0から1の範囲とする。`MarketRule` は `market_id: str`、`station_id: str`、`target_date: date`、`timezone: str`、`lower_bound_f: float | None`、`upper_bound_f: float | None`、`lower_inclusive: bool`、`upper_inclusive: bool`、`resolution_source: str` を持つ。

`src/weather_research/pit.py` では、`is_available_at_trade_time(record: object, trade_time: datetime) -> bool` と `filter_available_records(records: Iterable[object], trade_time: datetime) -> list[object]` を公開する。

`src/weather_research/probability.py` では、`ensemble_bucket_probability(values: Sequence[float], bucket: Bucket) -> float`、`gaussian_bucket_probability(mean: float, standard_deviation: float, bucket: Bucket) -> float`、`normalize_distribution(probabilities: Mapping[str, float]) -> dict[str, float]` を公開する。

`src/weather_research/execution.py` では、`executable_yes_price(observed_ask: float, spread: float, slippage: float) -> float` と `net_edge(model_probability: float, observed_ask: float, fee: float, spread: float, slippage: float, uncertainty_buffer: float) -> float` を公開する。

必須依存はPython 3.11以上、`pydantic`、`httpx`、`numpy`、`pytest` とする。Parquetを扱う実データ経路だけが必要になった場合に `duckdb` と `pyarrow` を追加する。Polymarketの注文署名、取引API、秘密鍵は依存に含めない。

## 変更履歴

- 2026-08-09: 初版。設計仕様書の承認後、New York固定の実データ研究とLevel 1–2バックテストへ分解した。
