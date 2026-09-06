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

設定は親ディレクトリの `.env` から読みます。`HL_test_wallet` は資金を持つHyperliquid Testnetの親（取引）口座アドレス、`HL_test_wallet_private_key` はその口座に認可されたAgent Walletの秘密鍵です。秘密値はプロジェクトへ複製しません。設定項目は [`.env.example`](.env.example) を参照してください。TraderとReviewerは `TRADER_MODEL` / `REVIEWER_MODEL` で個別に指定し、429（rate limit）の場合だけ各 `*_FALLBACK_MODEL` を1回試します。その他のエラーや応答形式不正ではモデルを暗黙に切り替えません。フォールバックも含め、preflightで全モデルの存在を確認します。

HyperliquidのUnified AccountではSpot USDCとPerp担保が共有されます。preflightは `userAbstraction` を自動取得し、Unified Accountなら `spotClearinghouseState` のUSDC `total` と `tokenToAvailableAfterMaintenance` をequity/available collateralとして使います。Perpの `clearinghouseState` は建玉・未実現損益・注文確認に引き続き使います。

Standard AccountでのみSpot→Perp内部移管が必要になる場合があります。内部移管はAgent Walletには許可されないため、現在の「親口座アドレス＋Agent秘密鍵」設定ではCLIがSDKへ送信する前に停止します。Standard Accountを親アカウントの秘密鍵で操作する一時的な環境で、対象口座に建玉・注文がないことを確認したうえで次を実行します。

```powershell
.\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file ..\.env transfer-to-perp --amount 2000
```

移管コマンドは指定額を超えて移さず、自動では実行しません。Unified Accountで実行すると「移管不要」として終了します。移管後に `preflight` を再実行してPerp `equity` と `available_collateral` を確認してください。

### Gemini無料枠（2026-09-06確認）

公式ドキュメント上、`gemini-3.5-flash-lite` は低遅延・高スループットのStableモデルで、Function CallingとStructured Outputsをサポートしています。Reviewerにはより余裕のある `gemini-3.6-flash` を選べます。[Traderモデル仕様](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)

料金表ではStandardのFree Tierについて、入力・出力（thinking tokensを含む）・context cachingが無料です。一方、無料枠ではGoogle Search/Maps groundingはAPIでは利用できず、Free Tierの入力はGoogle製品改善に利用される扱いです。従って、このBotはニュースgroundingを使わず、市場の加工済み特徴量だけを送ります。秘密鍵やAPIキーはプロンプトに含めません。[公式料金表](https://ai.google.dev/gemini-api/docs/pricing)

無料枠に固定の「安全なRPM/RPD」をコードへ埋め込むことはできません。RPM（分あたりリクエスト）、TPM（分あたりトークン）、RPD（1日あたりリクエスト）はモデル・プロジェクト・利用階層で変わり、APIキー単位ではなくプロジェクト単位です。RPDはPacific時間の深夜にリセットされ、表示値も保証値ではありません。実際の値は、対象プロジェクトを選んだGoogle AI StudioのDashboard > Rate limitsで運転前に確認してください。[公式レート制限](https://ai.google.dev/gemini-api/docs/rate-limits)

この実験の最大呼び出し数は、1時間でTrader 12 + Reviewer 2 = 14回、3時間で36 + 6 = 42回、24時間連続なら288 + 48 = 336回です。これはRPDを保証する数ではありません。`google-genai` SDKの再試行は`attempts=1`を明示し、429時だけ同じシフト内でフォールバックモデルを1回試します。フォールバックも失敗した場合は発注せず記録します。Reviewerの実運転は30分間隔だと1日48回になるため、Dashboardに表示されるReviewerモデルのRPDが20の場合は、2時間間隔（またはLiteモデル）へ調整してください。

無料枠は「無料料金」でも「無制限」ではありません。別のAPIキーを作っても同じプロジェクトのquotaは共有されます。モデル変更時は`.env`の4つのモデル変数を変更し、`preflight`でモデルの存在を確認したうえで、AI Studioの当日quotaと上記の予定呼び出し数を比較してください。無料枠の上限値はGoogle側で変更され得るため、固定値をREADMEやコードへ複製しません。[Billing FAQ](https://ai.google.dev/gemini-api/docs/billing)

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
