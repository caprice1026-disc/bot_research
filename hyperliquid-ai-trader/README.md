# Hyperliquid AI Trader v0.1

GeminiのFunction Callingで売買方向を判断し、Python側の固定Risk Engineを通してHyperliquid TestnetのperpetualへBracket注文を出すローカル実験環境です。v0.1はTestnet専用であり、Mainnet設定を起動時に拒否します。

既存の読み取り・署名確認CLI `hl-testnet-check` も互換機能として残しています。

## 安全境界

- Geminiへ公開する関数は `open_position` だけです。coin、size、leverage、network、walletは引数に含めません。
- Function Callingの自動実行は無効です。Pythonが一つの呼び出しだけを検証し、Risk Engineを通して一度だけ実行します。
- EntryはIOC、TP/SLは逆方向のreduce-only market triggerで、`normalTpsl` groupingの一括注文です。
- 応答不明、部分約定、保護注文失敗では既知のClient Order IDを記録し、取消と強制決済を試みます。
- 起動時に既存建玉、未約定注文、対象外銘柄の建玉があれば停止します。BotはSQLiteに記録したClient Order IDだけを管理します。
- `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` が正確に `http://127.0.0.1:9` の場合だけ、子プロセス内で解除します。`NO_PROXY` とUser/Machineの永続設定は変更しません。

## セットアップ

リポジトリ内の専用仮想環境を使います。

```powershell
cd C:\Users\Hodaka\Downloads\div\bot_research\hyperliquid-ai-trader
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Python 3.13の一時ディレクトリACLで `ensurepip` が失敗する場合は、システムpipから対象Pythonを指定します。

```powershell
python -m pip --python .venv\Scripts\python.exe install pip
python -m pip --python .venv\Scripts\python.exe install -e ".[test]"
```

設定は親ディレクトリの `.env` から読みます。`HL_test_wallet` は資金を持つHyperliquid Testnetの親（取引）口座アドレス、`HL_test_wallet_private_key` はその口座に認可されたAgent Walletの秘密鍵です。秘密値はプロジェクトへ複製しません。設定項目は [`.env.example`](.env.example) を参照してください。`GEMINI_MODEL` をTrader/Reviewer共通値として使い、必要な場合だけ `TRADER_MODEL`、`REVIEWER_MODEL` で個別に上書きできます。利用できないモデルへ暗黙にフォールバックしません。

HyperliquidのUnified AccountではSpot USDCとPerp担保が共有されます。preflightは `userAbstraction` を自動取得し、Unified Accountなら `spotClearinghouseState` のUSDC `total` と `tokenToAvailableAfterMaintenance` をequity/available collateralとして使います。Perpの `clearinghouseState` は建玉・未実現損益・注文確認に引き続き使います。

Standard AccountでのみSpot→Perp内部移管が必要になる場合があります。内部移管はAgent Walletには許可されないため、現在の「親口座アドレス＋Agent秘密鍵」設定ではCLIがSDKへ送信する前に停止します。Standard Accountを親アカウントの秘密鍵で操作する一時的な環境で、対象口座に建玉・注文がないことを確認したうえで次を実行します。

```powershell
.\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file ..\.env transfer-to-perp --amount 2000
```

移管コマンドは指定額を超えて移さず、自動では実行しません。Unified Accountで実行すると「移管不要」として終了します。移管後に `preflight` を再実行してPerp `equity` と `available_collateral` を確認してください。

Gemini無料枠の対象モデル、quota、入力データの取り扱いは変更される可能性があります。運転前に[公式料金表](https://ai.google.dev/gemini-api/docs/pricing)とGoogle AI Studioの対象プロジェクトquotaを確認してください。

## 実行

PowerShell wrapperは不正なProcess scope proxyを解除してからPythonを起動します。

```powershell
.\scripts\run.ps1 preflight
.\scripts\run.ps1 dry-run --cycles 36 --interval-seconds 0
.\scripts\run.ps1 canary
.\scripts\run.ps1 run-local
```

直接Pythonを実行する場合、グローバル引数の `--env-file` はサブコマンドより前です。

```powershell
.\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file ..\.env preflight
```

- `preflight`: Testnet、BTC市場、署名者認可、口座クリーン状態、指定Geminiモデルを読み取り確認します。
- `dry-run`: 実市場データとGeminiを使いますが、取引所へ注文を送りません。既定は36シフトを待ち時間なしで実行します。
- `canary`: Testnetへ2シフト発注し、それぞれを5分保有上限でcleanupした後、Reviewerを1回実行します。
- `run-local`: 5分ごとに36 Trader、30分ごとに6 Reviewerを実時間3時間運転します。
- `report --run-id <ID>`: SQLiteの確定約定、fee、user fundingから秘密情報を含まないレポートを再生成します。

実行中のSQLiteと詳細証跡は `data/trader.db`、匿名化済みレポートは `reports/` に保存されます。SQLite、`.env`、`.venv`、生ログはGit管理しません。

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Fake Adapterを用いて、LONG/SHORTのTP/SL方向、Decimal建玉上限、最小注文、日次損失、最大DD、部分約定、保護注文失敗、応答喪失、重複cycle、429、Reviewer Patch、scheduler、レポートを外部発注なしで検証します。

## 構成

```text
src/hyperliquid_ai_trader/
  agents.py              Gemini応答の検証と限定再試行
  cli.py                 preflight / dry-run / canary / run-local / report
  exchange/              取引所抽象interfaceと公式SDK adapter
  features.py            1分足120本・板・asset contextの特徴量化
  gemini_gateway.py      自動実行を無効にしたFunction Calling
  risk.py                変更不能なDecimal Risk Engine
  runner.py              run_once境界と5分/30分scheduler
  storage.py             監査用SQLite
  strategy.py            allowlist済みStrategy Patch
  trading_tools.py       モデルから呼べる唯一の注文経路
prompts/                  固定constitutionとReviewer指示
state/strategy.json       初期戦略仮説
```

Cloud Runのインフラはv0.1の対象外ですが、`TradingService.run_once()` と `review_once()` をJob境界として分離しています。

## 参照

- [Hyperliquid API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api)
- [公式Python SDK TP/SL例](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/examples/basic_tpsl.py)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
