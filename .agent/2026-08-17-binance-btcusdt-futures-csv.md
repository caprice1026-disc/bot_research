# Binance BTCUSDT先物CSV取得 ExecPlan

このExecPlanは生きた文書である。作業中は `PLANS.md` の要件に従い、`Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` を実態に合わせて更新する。

## 目的 / 全体像

この作業の完了後、研究者は `binance-btcusdt-futures-research` でコマンドを一度実行するだけで、Binance USDⓈ-M 無期限先物 `BTCUSDT` の直近365確定UTC日について、日足・1時間足・15分足のCSVを再取得できる。各CSVは公式アーカイブのチェックサムを確認してから時刻順に結合される。巨大な取得物と秘密情報はGitに追加せず、再現に必要なコード、テスト、説明だけをリモートへプッシュする。

## Progress

- [x] (2026-08-17 00:00Z) 対象をUSDⓈ-M `BTCUSDT` 無期限先物、期間を直前の完結UTC日までの365日と決定した。
- [x] (2026-08-17 00:00Z) 設計仕様を `docs/superpowers/specs/2026-08-17-binance-btcusdt-futures-data-design.md` として作成し、利用者の承認を得た。
- [x] (2026-08-17 13:52Z) 取得・結合・検証ロジックをテスト駆動で実装した。単体テストは5件通過した。
- [x] (2026-08-17 13:53Z) 公式アーカイブから3足種を取得し、CSVとメタデータを検証した。日足365行、1時間足8,760行、15分足35,040行である。
- [ ] `.gitignore` を監査・更新し、コードのみをコミットして既存の未プッシュコミットとともにプッシュする。

## Surprises & Discoveries

- Observation: 公式リポジトリは先物KlineについてUSD-MとCOIN-Mを区別し、日次・月次アーカイブと同じ場所のSHA-256チェックサムを提供する。
  Evidence: `https://github.com/binance/binance-public-data` のREADMEにFutures KlinesとCHECKSUMの説明がある。
- Observation: 環境のループバックプロキシを継承した`urllib`接続はWinError 10061で失敗した。
  Evidence: 初回取得は `...zip.CHECKSUM: <urlopen error [WinError 10061]>` で終了し、`ProxyHandler({})` による直接接続後は全126アーカイブのSHA-256照合とCSV検証が通過した。

## Decision Log

- Decision: 対象を `BTCUSDT` USDⓈ-M perpetual とする。
  Rationale: 利用者が承認した、最も流動性が高いUSDT建てBTC無期限先物である。COIN-M、現物、限月は混ぜない。
  Date/Author: 2026-08-17 / Codex と利用者。
- Decision: 確定済みの過去月は月次ZIP、対象開始月と当月の範囲は日次ZIPを使う。
  Rationale: 365個ずつの日次ZIPを避けながら、月次アーカイブが未公開の当月分を完全にする。
  Date/Author: 2026-08-17 / Codex。
- Decision: `raw/`、`data/`、`metadata/` はGit管理外にする。
  Rationale: 取得物は大容量かつ再生成可能で、メタデータには環境・取得時刻が入り変更が頻繁なため、コードレビューとリモート履歴を汚さない。
  Date/Author: 2026-08-17 / Codex。

## Outcomes & Retrospective

未完了。最終更新時に、生成CSVの行数・時間境界・チェックサム検証結果、Gitで追跡するファイル、リモートへのプッシュ結果をここへ記録する。

## 背景と構成

リポジトリのルート `C:\Users\Hodaka\Downloads\div\bot_research` には別研究 `base-lp-research` と `weather-polymarket-research` がある。今回の成果物はそれらを変更せず、ルート直下の新規 `binance-btcusdt-futures-research/` に閉じ込める。`src/` は今回使用しない。

新規ディレクトリの役割は次の通りである。`download_binance_klines.py` は取得、チェックサム照合、結合、検証を担うPython標準ライブラリだけのCLIである。`tests/test_download_binance_klines.py` はネットワークに依存しない単体テストである。`raw/` は公式ZIPと`.CHECKSUM`、`data/` は研究用CSV、`metadata/` は実行ごとの取得証跡JSONであり、後三者はGit管理外である。`README.md` は再実行方法と列定義を説明する。

「確定UTC日」とは、実行日を含めず、実行日の00:00 UTCより前に終わった一日をいう。2026-08-17に実行すると対象は2025-08-17 00:00:00 UTCから2026-08-16 23:59:59.999 UTCまでとなる。BinanceのKlineは開始時刻、始値、高値、安値、終値、出来高、終了時刻、建値換算出来高、取引回数、成行買い数量2列、ignore列の12列である。

## 作業計画

### Milestone 1: 再現可能な取得器をテストで定義する

この段階では、ネットワークや実データなしで期間分割、URL、CSV正規化、連続性検査が正しいことを証明する。完了時には単体テストが、実装前には期待どおり失敗し、実装後には通過する。

対象ファイルは新規 `binance-btcusdt-futures-research/tests/test_download_binance_klines.py` と `binance-btcusdt-futures-research/download_binance_klines.py` である。テストは `archive_requests(start_date, end_date, interval)` が2025-08-17から2026-08-16の例に対して、2025-08-17から31の日次、2025-09から2026-07の月次、2026-08-01から16の日次だけを返すことを要求する。`archive_url(request)` は `https://data.binance.vision/data/futures/um/{daily|monthly}/klines/BTCUSDT/{interval}/...zip` を返すことを要求する。`normalize_and_validate(rows, interval, start_ms, end_ms)` はヘッダーなしの12列を名前付き列へ変換し、対象範囲に絞り、重複・欠落・不正な順序で `ValueError` を出すことを要求する。

各テストを `.venv\\Scripts\\python.exe -m pytest -q binance-btcusdt-futures-research/tests/test_download_binance_klines.py` で個別に実行し、未実装による失敗を確認してから最小の実装を追加する。実装は `argparse`、`csv`、`datetime`、`hashlib`、`json`、`pathlib`、`urllib.request` だけを用い、追加依存を入れない。HTTP取得は短いタイムアウトと最大3回の指数バックオフを持たせ、既存ファイルはチェックサム一致時だけ再利用する。

### Milestone 2: 公式データを取得してCSVを生成する

この段階ではMilestone 1のCLIを実データへ適用する。実行前に `raw/`、`data/`、`metadata/` を作成する。各リクエストでZIPと `.CHECKSUM` を取得し、公開されたSHA-256値と比較する。一件でも不一致または取得不能なら非ゼロで終了し、完成CSVを置換しない。

作業ディレクトリ `binance-btcusdt-futures-research` で次を実行する。

    ..\\.venv\\Scripts\\python.exe download_binance_klines.py --end-date 2026-08-17

CLIは `data/BTCUSDT-1d-365d.csv`、`data/BTCUSDT-1h-365d.csv`、`data/BTCUSDT-15m-365d.csv` と `metadata/fetch-2026-08-17.json` を出力する。CSVは `open_time_utc,open_time_ms,open,high,low,close,volume,close_time_ms,quote_asset_volume,number_of_trades,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore` の13列である。

出力後、CLIの検証結果と別の読み取り専用検査で、CSVの先頭・末尾時刻、重複ゼロ、日足365行、1時間足8,760行、15分足35,040行を確認する。結果をこの文書の `Surprises & Discoveries` と `Outcomes & Retrospective` に転記する。

### Milestone 3: Git追跡範囲を固定しリモートへ公開する

この段階では、`.gitignore` の既存ルールを先に確認する。`git check-ignore -v` で `.env`、Pythonキャッシュ、既存研究の生成物の扱いを確認し、今回の `binance-btcusdt-futures-research/raw/`、`data/`、`metadata/`、`.pytest_cache/`、`__pycache__/`、`*.zip` を必要最小限の規則で無視する。無差別に既存の研究成果や設定を無視してはならない。`download_binance_klines.py`、テスト、README、設計書、ExecPlanは追跡対象に残す。

`git status -sb` と `git diff --check` で、意図するソース・文書・`.gitignore`だけが未コミット変更であり、巨大CSV・ZIP・メタデータ・`.env`が追跡候補にないことを確認する。明示パスで追加してコミットする。続けて全テストとデータ検証を再実行し、現在のブランチにある既存の未プッシュコミットも含めて `git push -u origin codex/base-uniswap-v3-lp-research` を実行する。push後には `git status -sb` と `git log origin/codex/base-uniswap-v3-lp-research..HEAD` がクリーンであることを確認する。

## 具体的な検証

Milestone 1では、テストを先に作り、関数不在または未実装による失敗を記録する。実装後は同じテスト全件を実行し、失敗が0件であることを確認する。

Milestone 2では、取得CLIが各CSVの `row_count` と時間境界をJSONへ書くことを確認し、以下を実行する。

    ..\\.venv\\Scripts\\python.exe download_binance_klines.py --end-date 2026-08-17 --verify-only

成功時は3ファイル全てについて期待行数、0重複、0欠落を表示する。失敗時はどの足種・期待時刻・実際の時刻が違うかを表示する。

Milestone 3では `git check-ignore -v binance-btcusdt-futures-research/raw/example.zip` が該当するignore規則を示し、`git check-ignore binance-btcusdt-futures-research/download_binance_klines.py` が非ゼロになることを確認する。これによりコードは追跡され、生成データは追跡されないことを示す。

## 冪等性と復旧

同じ `--end-date` を再実行しても、ハッシュが一致する既存ZIPを再利用し、最終CSVは一時ファイルから検証後に置換される。不完全なZIPまたはチェックサム不一致は削除せず失敗として残し、次回の再取得で上書きできる。ネットワーク障害時は最大3回の再試行後に終了し、原因URLを表示する。作業者は接続を回復して同じコマンドを再実行できる。

## 成果物と依存関係

本作業はPython標準ライブラリとBinance公式 `data.binance.vision` のみを使う。公開インターフェースは次の通りである。

    archive_requests(start_date: date, end_date: date, interval: str) -> list[ArchiveRequest]
    archive_url(request: ArchiveRequest) -> str
    normalize_and_validate(rows: Iterable[list[str]], interval: str, start_ms: int, end_ms: int) -> list[dict[str, str]]
    main(argv: Sequence[str] | None = None) -> int

`ArchiveRequest` は `kind`（`daily`または`monthly`）、`interval`、`date_or_month`、`symbol`を持つ不変のデータ構造とする。実行日は `--end-date YYYY-MM-DD` で明示でき、省略時はUTC現在日を用いる。

## 変更履歴

2026-08-17: 利用者承認後、設計仕様を実装・実取得・Git公開に分解し、追跡しないデータ範囲と検証コマンドを明記した。
