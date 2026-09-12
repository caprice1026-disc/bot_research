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

設定は親ディレクトリの `.env` から読みます。`HL_test_wallet` は資金を持つHyperliquid Testnetの親（取引）口座アドレス、`HL_test_wallet_private_key` はその口座に認可されたAgent Walletの秘密鍵です。秘密値はプロジェクトへ複製しません。設定項目は [`.env.example`](.env.example) を参照してください。Traderは `gemini-3.5-flash-lite`、Reviewerは `gemini-3.6-flash`を指定します。現行設定ではフォールバックモデルを設定せず、429（rate limit）時はそのシフトを発注せず記録します。別モデルを使う場合だけ各 `*_FALLBACK_MODEL` を指定できます。

HyperliquidのUnified AccountではSpot USDCとPerp担保が共有されます。preflightは `userAbstraction` を自動取得し、Unified Accountなら `spotClearinghouseState` のUSDC `total` と `tokenToAvailableAfterMaintenance` をequity/available collateralとして使います。Perpの `clearinghouseState` は建玉・未実現損益・注文確認に引き続き使います。

Standard AccountでのみSpot→Perp内部移管が必要になる場合があります。内部移管はAgent Walletには許可されないため、現在の「親口座アドレス＋Agent秘密鍵」設定ではCLIがSDKへ送信する前に停止します。Standard Accountを親アカウントの秘密鍵で操作する一時的な環境で、対象口座に建玉・注文がないことを確認したうえで次を実行します。

```powershell
.\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file ..\.env transfer-to-perp --amount 2000
```

移管コマンドは指定額を超えて移さず、自動では実行しません。Unified Accountで実行すると「移管不要」として終了します。移管後に `preflight` を再実行してPerp `equity` と `available_collateral` を確認してください。

### Gemini無料枠（2026-09-06確認）

公式ドキュメント上、`gemini-3.5-flash-lite` は低遅延・高スループットのStableモデルで、Function CallingとStructured Outputsをサポートしています。Reviewerには `gemini-3.6-flash` を指定します。[Traderモデル仕様](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)

料金表ではStandardのFree Tierについて、入力・出力（thinking tokensを含む）・context cachingが無料です。一方、無料枠ではGoogle Search/Maps groundingはAPIでは利用できず、Free Tierの入力はGoogle製品改善に利用される扱いです。従って、このBotはニュースgroundingを使わず、市場の加工済み特徴量だけを送ります。秘密鍵やAPIキーはプロンプトに含めません。[公式料金表](https://ai.google.dev/gemini-api/docs/pricing)

無料枠に固定の「安全なRPM/RPD」をコードへ埋め込むことはできません。RPM（分あたりリクエスト）、TPM（分あたりトークン）、RPD（1日あたりリクエスト）はモデル・プロジェクト・利用階層で変わり、APIキー単位ではなくプロジェクト単位です。RPDはPacific時間の深夜にリセットされ、表示値も保証値ではありません。実際の値は、対象プロジェクトを選んだGoogle AI StudioのDashboard > Rate limitsで運転前に確認してください。[公式レート制限](https://ai.google.dev/gemini-api/docs/rate-limits)

この実験の最大呼び出し数は、1時間でTrader 12 + Reviewer 1 = 13回、3時間で36 + 2 = 38回、24時間連続なら288 + 12 = 300回です。これはRPDを保証する数ではありません。`google-genai` SDKの再試行は`attempts=1`を明示し、429時はフォールバックせず発注せず記録します。Reviewerは2時間間隔（`REVIEW_INTERVAL_SECONDS=7200`）です。`TAKER_FEE_PCT=0.045` はHyperliquid Perpsのbase tier taker fee 0.045%を初期値とし、実口座のfee tierに合わせて変更してください。[Hyperliquid Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)

無料枠は「無料料金」でも「無制限」ではありません。別のAPIキーを作っても同じプロジェクトのquotaは共有されます。モデル変更時は`.env`のTrader/Reviewerモデル変数を変更し、`preflight`でモデルの存在を確認したうえで、AI Studioの当日quotaと上記の予定呼び出し数を比較してください。無料枠の上限値はGoogle側で変更され得るため、固定値をREADMEやコードへ複製しません。[Billing FAQ](https://ai.google.dev/gemini-api/docs/billing)

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
- `run-local`: 5分ごとに36 Trader、設定した間隔（既定2時間）でReviewerを実時間3時間運転します。
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

## オフライン研究v3（実装中）

`src/hyperliquid_ai_trader/research/`には、ライブ注文経路と分離した研究用の基盤があります。現在は、判断時点で利用可能な確定1分足だけから`common_candles_v1`を計算し、1分足上でEntry遅延、SL/TP、300秒保有上限、fee、spread、slippageを再現できます。同一足でSLとTPへ到達した場合は、主結果のSL先と感度分析のTP先を明示的に選び、曖昧な結果として記録します。

研究台帳はライブ用`data/trader.db`とは別のSQLiteへ作成します。数量がflatになったepisodeだけを確定証拠とし、損益ゼロも除外しません。失敗したreviewへ送った証拠は評価済みにせず、正常な空patchまたは正常patchでのみ消費します。`shadow`（見送り時の仮想結果）は仮想口座へ加算されません。

固定ルールのReplayはネットワーク、秘密鍵、Gemini APIを必要としません。公開足の`collect`だけは署名なしのHyperliquid Mainnet Info APIへ接続しますが、秘密鍵・wallet・Gemini APIは使いません。研究v3全体のBatch、LLM Replay、Reviewer、Forward比較CLIは未実装であり、既存Testnet Botの`dry-run`をオフラインSimulatorの代用にはしないでください。

公開JSONの研究設定は、課金を明示許可しない限りモデルAPIを呼べません。まず設定だけを確認できます。

```powershell
.\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli validate-config --config configs\research\development.json
```

Hyperliquid公開1分足は、最大5,000本の単一スナップショットだけを明示的に取得できます。確定足だけをJSONLへ保存し、同じ場所に取得範囲・受信時刻・内容hashを含むmanifestを作ります。空結果は`insufficient_data`で終了し、空の損益結果にはしません。

```powershell
.\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli collect `
  --config configs\research\development.json `
  --start-ms 1757462400000 `
  --end-ms 1757480400000 `
  --output data\research\BTC-1m.jsonl
```

正規化済みの1分足JSONLがある場合は、外部APIなしで3つの固定ルールを時系列順に再生できます。`insufficient_data` は、必要な61本の事前足またはEntry/Exit用の後続足が不足し、損益ゼロとして評価していないことを表します。

```powershell
.\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli baseline `
  --config configs\research\development.json `
  --candles data\research\BTC-1m.jsonl `
  --baseline momentum
```

## 参照

- [Hyperliquid API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api)
- [公式Python SDK TP/SL例](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/examples/basic_tpsl.py)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
