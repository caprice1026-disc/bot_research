# Hyperliquidテストネット接続確認を再現可能にする

このExecPlanは生きた文書である。作業中は `Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` を更新し続ける。本計画はリポジトリルートの `PLANS.md` に従って管理する。

## Purpose / Big Picture

この変更により、利用者はリポジトリ直下の `.env` に設定したHyperliquid用ウォレットと秘密鍵が、Hyperliquidテストネットで実際に利用できるかを一つの再実行可能なPythonコマンドで確認できる。実行は市場情報とアカウント状態を公開APIから読み、さらに資産を動かさない署名付き `noop` を送る。成功時は `results/connection_check.json` に秘密情報を含まない証跡が残る。

## Progress

- [x] (2026-08-22 17:12+09:00) リポジトリ構成、`.env` の変数設定状態、Python環境、公式APIと公式SDKの `noop` 対応を確認した。
- [x] (2026-08-22 17:12+09:00) 設計を `docs/superpowers/specs/2026-08-22-hyperliquid-testnet-connectivity-design.md` に記録した。
- [x] (2026-08-22 17:18+09:00) TDDで設定検証、接続結果分類、CLI、公式SDK境界を実装し、各機能について失敗から成功へのテスト遷移を確認した。
- [x] (2026-08-22 17:19+09:00) `hyperliquid-testnet-research/.venv` を作り、公式SDK 0.24.0、pip 24.3.1、テスト依存を導入した。
- [x] (2026-08-22 17:23+09:00) 全12テストを通し、テストネットで公開Info API、署名者の対象ウォレットへの紐付け、署名付き `noop` を実行した。
- [x] (2026-08-22 17:23+09:00) 結果JSON、README、計画の実績欄を最終状態へ更新した。

## Surprises & Discoveries

- Observation: 現在のシステムPythonは3.13.1で、`pytest` は利用可能だが `hyperliquid` と `python-dotenv` は未導入である。
  Evidence: `importlib.util.find_spec` の確認結果が `hyperliquid_sdk=MISSING`、`dotenv=MISSING`、`pytest=AVAILABLE` だった。
- Observation: 公式SDKには `Exchange.noop(nonce)` があり、テストネットURLの判定を署名処理へ渡している。
  Evidence: 公式 `hyperliquid-dex/hyperliquid-python-sdk` の `hyperliquid/exchange.py` で `noop` が `sign_l1_action` と `/exchange` を使うことを確認した。
- Observation: Python 3.13の `venv` 作成では、`ensurepip` が `%TEMP%` と作業ツリー内の一時ディレクトリの両方でファイルACLに拒否された。
  Evidence: wheelコピー時に `PermissionError: [Errno 13]` が発生したが、システムpipの `--python .venv\Scripts\python.exe` で同じ仮想環境へSDKとpip本体を導入できた。
- Observation: `HL_test_wallet` と秘密鍵由来署名者は異なるが、署名付き `noop` は成功した。
  Evidence: 最終結果は `signer_matches_target=false`、`signer_role=agent`、`signer_authorized_for_target=true`、`signed_noop.ok=true` を同時に記録した。公式 `userRole` 応答で対象アカウントへの紐付けも確認した。
- Observation: pytestの既定一時領域も、先行実行が残した `%TEMP%\pytest-of-Hodaka` のACLにより再実行時に列挙できなかった。
  Evidence: 固定 `--basetemp` も次回実行時の削除で失敗したため、CLIテストは実行ごとにGit除外対象の一意な `pytest-cache-files-hl-<UUID>` を使う設計にした。最終の全12件を連続2回実行し、12件成功を2回確認した。

## Decision Log

- Decision: 署名を独自実装せず公式 `hyperliquid-python-sdk` を使う。
  Rationale: 署名形式とテストネット判定を公式実装へ委ね、誤署名や将来の仕様差分を減らすため。
  Date/Author: 2026-08-22 / Codex
- Decision: `HL_test_wallet` と秘密鍵由来アドレスの不一致を直ちにエラーにしない。
  Rationale: Hyperliquidではマスターアカウントと承認済みAPIウォレットの署名者が異なる構成が正式にサポートされるため。
  Date/Author: 2026-08-22 / Codex
- Decision: 署名検証には注文でなく `noop` を一度だけ使う。
  Rationale: 注文や残高変更なしで秘密鍵、署名、nonce、テストネットのExchange API経路をまとめて検証できるため。
  Date/Author: 2026-08-22 / Codex

## Outcomes & Retrospective

専用の `hyperliquid-testnet-research/` にPythonパッケージ、12件の単体テスト、README、専用 `.venv`、実接続証跡を作成した。公式SDK 0.24.0による最終実行では `https://api.hyperliquid-testnet.xyz` へ到達し、市場210件のメタデータ、対象アカウント状態、署名者が対象アカウントへ認可されたagentであること、署名付き `noop` がすべて成功した。注文や送金は実行していない。

Python 3.13の `ensurepip` は環境のACL制約で失敗したが、システムpipの対象Python指定により仮想環境を削除せず復旧できた。この代替手順はREADMEへ残した。

## Context and Orientation

作業ルートは `C:\Users\Hodaka\Downloads\div\bot_research` である。秘密情報はこのルートの `.env` にあり、既存 `.gitignore` は `.env` と `.venv` を除外している。新しいコードはすべて `hyperliquid-testnet-research/` に置く。Pythonパッケージ名は `hl_testnet_check` とし、エントリーポイントは `python -m hl_testnet_check.cli` とする。

HyperliquidのInfo APIは市場やユーザー状態を読む公開HTTP APIである。Exchange APIは署名付きアクションを送るHTTP APIである。`noop` は「何もしない」署名付きアクションで、資産や注文を変更しないが、再送攻撃防止用の一意な数値であるnonceを消費する。APIウォレットはマスターアカウントの代わりに署名するため承認された別アドレスであり、ユーザー状態の照会にはAPIウォレットでなくマスターアカウントを使う。

## Plan of Work

最初のマイルストーンでは、`hyperliquid-testnet-research/tests/test_connection_check.py` を先に作り、まだ存在しない `hl_testnet_check.connection` の公開インターフェースを呼ぶ。環境変数不足、不正ウォレット、公開API成功、署名付き `noop` 成功、外部APIエラーの振る舞いをテストし、実装前に意図した理由で失敗することを確認する。

次のマイルストーンでは、`src/hl_testnet_check/connection.py` に純粋な設定検証と、SDK境界を受け取る接続オーケストレーターを実装する。`src/hl_testnet_check/cli.py` は `.env` の読込、公式SDKの `Info` と `Exchange` の組み立て、秘密情報を含まないJSONの表示と保存だけを担当する。SDKの実ネットワーク呼び出しは単体テストで行わず、テスト後の統合実行で一度だけ行う。

最後のマイルストーンでは、`hyperliquid-testnet-research/.venv` を作成し、`pyproject.toml` の依存を導入する。専用Pythonで全テストを実行してからCLIを実行する。CLIが終了コード0、公開API成功、`noop` 成功を返すことを受け入れ条件とする。READMEにはPowerShellでの作成、インストール、再実行、出力項目、`noop` のnonce消費を記載する。

## Concrete Steps

すべてのコマンドはリポジトリルート `C:\Users\Hodaka\Downloads\div\bot_research` から実行する。

まずテストと最小パッケージ設定を作り、次を実行する。

    python -m pytest hyperliquid-testnet-research\tests -q -p no:cacheprovider

実装前は `ModuleNotFoundError: No module named 'hl_testnet_check'` または未実装関数による失敗を期待する。次に `src/hl_testnet_check/connection.py` と `cli.py` を実装し、同じコマンドが成功するまで最小修正する。

仮想環境と依存を導入する。

    python -m venv hyperliquid-testnet-research\.venv
    hyperliquid-testnet-research\.venv\Scripts\python.exe -m pip install -e "hyperliquid-testnet-research[test]"

その後、専用Pythonで単体テストと実接続を実行する。

    hyperliquid-testnet-research\.venv\Scripts\python.exe -m pytest hyperliquid-testnet-research\tests -q -p no:cacheprovider
    hyperliquid-testnet-research\.venv\Scripts\python.exe -m hl_testnet_check.cli --env-file .env --output hyperliquid-testnet-research\results\connection_check.json

成功時のJSONは `overall_status` が `ok`、`network` が `testnet`、`public_api.ok` と `signed_noop.ok` がともに `true` になる。秘密鍵、署名、完全な環境変数値は含まれない。

## Validation and Acceptance

単体テストでは、設定値の不足や形式エラーが外部API呼び出し前に分類されること、Info APIの戻り値から市場数とユーザー状態取得成功が記録されること、`noop` の `status=ok` だけが署名成功になることを確認する。各テストは対応する本番コードが欠けると失敗し、SDKのモック自体ではなくオーケストレーターが返す結果を検証する。

統合実行では専用 `.venv` のPythonを使い、`results/connection_check.json` と標準出力を確認する。公開APIだけ成功して `noop` が失敗した場合、ネットワーク到達は成功、署名またはAPIウォレット承認は失敗として分ける。HTTPエラー時も秘密情報を出さず、終了コード1と失敗段階を残す。

## Idempotence and Recovery

仮想環境作成、編集可能インストール、単体テスト、公開API照会は繰り返してよい。`noop` は毎回新しいミリ秒nonceを使うため再実行可能だが、実行ごとにnonceを一つ消費する。途中で依存導入が失敗した場合は既存 `.venv` を削除せず、同じpipコマンドを再実行する。結果JSONは秘密情報を含まず、同じパスへ安全に更新できる。

## Artifacts and Notes

最終成果物は `hyperliquid-testnet-research/pyproject.toml`、`README.md`、`src/hl_testnet_check/`、`tests/`、`results/connection_check.json` である。秘密鍵は成果物に含めていない。最終証跡は次のとおりである。

    12 passed in 0.38s
    12 passed in 0.34s (immediate second run)
    overall_status=ok
    api_url=https://api.hyperliquid-testnet.xyz
    public_api.ok=true, market_count=210, user_state_ok=true
    signer_matches_target=false, signer_role=agent
    signer_authorized_for_target=true
    signed_noop.ok=true, response_status=ok

## Interfaces and Dependencies

`src/hl_testnet_check/connection.py` は `ConnectionConfig(wallet_address: str, private_key: str)`、`validate_config(config, derive_signer_address) -> ValidatedConfig`、`run_connection_check(config, sdk_gateway, nonce_factory) -> dict[str, object]` を公開する。`sdk_gateway` は `api_url: str`、`get_meta() -> dict`、`get_user_state(address: str) -> dict`、`get_user_role(address: str) -> dict`、`send_noop(nonce: int) -> dict` を持つ。`src/hl_testnet_check/cli.py` はこの境界を公式SDKへ接続する。

必須依存はPython 3.12以上、`hyperliquid-python-sdk`、`python-dotenv` とする。テスト依存は `pytest` とする。テストネットURLはSDKの `hyperliquid.utils.constants.TESTNET_API_URL` を使い、URL文字列を独自に複製しない。

## 変更履歴

- 2026-08-22: 初版。ユーザーが専用ディレクトリ、専用仮想環境、公式Python SDK、確認なしの最後までの実行を指定したため作成した。
- 2026-08-22: 実装完了。TDD、Python 3.13の仮想環境復旧、公式SDK 0.24.0による公開APIと署名付き `noop` の成功証跡を反映した。
- 2026-08-22: 最終検証でpytest一時領域のACL問題を確認し、テスト実行ごとにプロジェクト内のGit除外済み一意ディレクトリを使う方式へ変更した。
- 2026-08-22: `userRole` によるagentと対象ウォレットの紐付け確認を追加し、不一致時は `noop` を送らない安全条件を追加した。
