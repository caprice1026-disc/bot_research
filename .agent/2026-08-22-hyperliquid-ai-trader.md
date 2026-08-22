# Hyperliquid Testnetで自己改訂型AIトレーダーを運転する

このExecPlanは生きた文書である。作業中は `Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` を更新し続ける。本計画はリポジトリルートの `PLANS.md` に従って管理する。

## Purpose / Big Picture

この変更により、利用者はHyperliquid TestnetのBTC perpetualを、5分ごとのGemini判断と30分ごとの戦略レビューで自動運転できる。銘柄、レバレッジ、建玉上限、損失上限は環境変数で設定できるが、Geminiはこれらを変更できない。注文はEntry、Take Profit、Stop Lossを一括して出し、全入力、判断、注文、約定、戦略版をSQLiteへ保存する。まずDry-runと二シフトのCanaryで安全性を証明し、mainへ公開した後に三時間運転と匿名化レポートを追加公開する。

## Progress

- [x] (2026-08-22 18:05+09:00) ユーザーとの設計確認、公式SDK/API調査、Testnet口座の読み取り確認を完了した。
- [x] (2026-08-22 18:12+09:00) 既存接続確認の12テストが成功する基準状態を確認し、`codex/hyperliquid-ai-trader-v01` ブランチを作成した。
- [x] (2026-08-22 18:22+09:00) `hyperliquid-testnet-research` を `hyperliquid-ai-trader` へ改名し、既存接続確認を維持した。
- [x] (2026-08-22 18:31+09:00) 設定、Proxy解除、市場特徴量、Risk EngineをTDDで実装した。
- [x] (2026-08-22 18:48+09:00) 取引所抽象化、Hyperliquid bracket注文、SQLite永続化をTDDで実装した。
- [ ] Gemini Trader/Reviewer、ローカルrunner、レポートをTDDで実装する。
- [ ] 全テスト、Dry-run、二シフトCanary、cleanup検証を完了する。
- [ ] Canary成果をmainへ初回pushする。
- [ ] 三時間運転を完了し、匿名化要約をmainへ再pushする。

## Surprises & Discoveries

- Observation: 現在のプロセスには `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` が `http://127.0.0.1:9` として注入されているが、User/Machine scopeには永続設定がない。
  Evidence: .NETのEnvironment APIでProcess/User/Machineを個別に確認した。ネットワークを使う子プロセスでは同じシェル内で三変数を解除する必要がある。
- Observation: Testnet口座は開始時点でequity 1000 USDC、使用証拠金0、建玉0である。
  Evidence: Proxyを使わない読み取り専用Info API呼び出しの `marginSummary` と `assetPositions` を確認した。
- Observation: 公式SDK 0.24.0は `bulk_orders` の `normalTpsl` grouping、IOC注文、trigger TP/SL、Client Order IDをサポートする。
  Evidence: 導入済みSDKのシグネチャと公式 `examples/basic_tpsl.py` を確認した。

## Decision Log

- Decision: 既存ディレクトリを `hyperliquid-ai-trader` へ改名し、接続確認CLIを互換機能として残す。
  Rationale: 署名者認可とTestnet接続確認を重複実装せず、実注文前のpreflightへ再利用するため。
  Date/Author: 2026-08-22 / User and Codex
- Decision: Geminiには安全な `open_position` Functionだけを公開し、自動Function実行を無効化する。
  Rationale: 一サイクル一注文とPython側のRisk Engineを必ず通し、LLMへcoin、size、leverageを渡さないため。
  Date/Author: 2026-08-22 / User and Codex
- Decision: 既定リスクはBTC、5倍isolated、1回1%、建玉250 USDC、日次損失20%、最大DD25%とする。
  Rationale: ユーザーがTestnet用の攻めた実験設定を選択したため。
  Date/Author: 2026-08-22 / User
- Decision: Canary後にmainへ初回pushし、三時間運転後に匿名化要約を二回目のcommit/pushで追加する。
  Rationale: 実装を早期に保全しつつ、長時間実験結果をコード変更と分離するため。
  Date/Author: 2026-08-22 / User

## Outcomes & Retrospective

未完了。各マイルストーン完了時に、実装結果、検証証跡、残課題を追記する。

## Context and Orientation

作業ルートは `C:\Users\Hodaka\Downloads\div\bot_research` である。既存の `hyperliquid-testnet-research` には公式Python SDKを使った接続確認、署名者のagent認可確認、資産を動かさない署名付きnoop、12件のテスト、専用 `.venv` がある。このディレクトリを `hyperliquid-ai-trader` へ改名し、新しい `hyperliquid_ai_trader` パッケージを同居させる。ルート `.env` と専用 `.venv` はGit除外対象であり、秘密情報を新しいファイルへ複製しない。

Traderは市場観測からLONGまたはSHORTをFunction Callとして提案するGemini役である。Reviewerは確定した取引群から戦略JSONへの許可済みPatchを提案する別のGemini役である。Risk Engineは損失許容額から建玉量を計算する変更不能なPythonコードである。Bracket注文はEntryと、それを閉じるTP/SLを一つの関連注文群として送る。Client Order IDは再試行時に同じ注文を照合するための128 bit識別子である。

## Plan of Work

最初のマイルストーンではディレクトリを改名し、設定とネットワークpreflightを実装する。Process scopeの不正なループバックProxyだけを解除し、Testnet以外を拒否する。市場特徴量とRisk Engineは外部通信を持たない純粋関数として、失敗テストから実装する。

次のマイルストーンでは `TradingExchange` とSQLite repositoryを作る。Hyperliquid adapterは公式SDK型を内部へ漏らさず、市場・口座読み取り、レバレッジ更新、`normalTpsl` bracket、決済、取消、約定、Fundingを提供する。すべての実注文にサイクル由来のClient Order IDを付け、応答不明時は照会してから再試行する。

第三のマイルストーンではGemini Function Calling、ReviewerのJSON Patch、5分/30分runner、レポートを追加する。Traderの自動Function実行は禁止し、検証済みの一Function CallだけをTradingToolsへ渡す。Reviewer Patchはstrategy配下の許可済みパス以外を拒否する。

最後に全テストと加速Dry-runを通し、Testnetで二シフトCanaryを行う。終了時の全建玉・注文ゼロを確認してmainへ初回pushする。その後、36 Traderシフトと6 Reviewerを実時間で運転し、終了cleanup、匿名化、レポート検証後に二回目をpushする。

## Concrete Steps

コマンドはリポジトリルートから実行する。Pythonコマンドは改名後の `hyperliquid-ai-trader\.venv\Scripts\python.exe` を使う。pytestのACL問題を避けるため、各実行でGit除外対象の一意な `--basetemp` を指定する。

    git status -sb
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m pytest hyperliquid-ai-trader\tests -q --basetemp <unique-path>
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli preflight --env-file .env
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli dry-run --cycles 36 --interval-seconds 0
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli canary --cycles 2
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli run-local --duration-hours 3

Canaryと三時間運転は、同じPowerShellプロセス内で三つのProxy変数を解除してから起動する。各段階後に `git status`、`git diff --check`、pytest、秘密情報検査を実行する。

## Validation and Acceptance

単体テストは設定不足、Mainnet拒否、Proxy解除、LONG/SHORTのTP/SL方向、Decimal建玉量、最小10 USD、日次損失、最大DD、不正Function Call、複数Function Call、429、部分約定、保護注文失敗、応答喪失、重複Client Order ID、Reviewer不正Patch、SQLite再起動、schedulerの遅延、レポート集計を検証する。

Dry-runは36シフトと6レビューを待ち時間なしで完了し、外部へ注文を送らない。Canaryは二つの実注文episodeを作り、各EntryにTP/SLが関連し、終了時に対象口座の全建玉と未約定注文がゼロになる。三時間運転は36予定シフトと6予定レビューを保存し、外部障害によるskipを成功取引に数えない。最終レポートは厳密なfills、fees、user fundingと、1分足推定MFE/MAEを区別する。

## Idempotence and Recovery

run IDとスロット番号にはSQLite一意制約を持たせ、同じslotの再実行は既存状態を返す。注文の再送前にClient Order IDを照会する。途中終了後は既知のBot注文だけを取消し、既知のポジションを閉じてから再開する。未知の既存建玉または注文がある場合は自動操作せず停止する。三時間終了時のcleanupを確認できなければ新規注文と二回目のpushを停止する。

## Artifacts and Notes

Git管理する成果物はソース、テスト、prompts、初期strategy、README、`.env.example`、ExecPlan、秘密情報を除いたCanary/三時間要約である。SQLite、生Gemini応答、完全ログ、`.env`、`.venv` はGit管理しない。レポートにはウォレットアドレス、秘密鍵、署名、生プロンプトを含めない。

## Interfaces and Dependencies

`TradingExchange` は市場・口座状態、`set_leverage`、`place_bracket`、`close_position`、`cancel_bot_orders`、`get_fills`、`get_user_funding` を公開する。`RiskEngine.create_plan(decision, market, account)` は検証済み `OrderPlan` を返す。`TradingTools.open_position(side, stop_loss_pct, take_profit_pct, confidence, thesis, would_abstain, abstain_reason)` は一サイクル一回だけRisk Engineと取引所へ接続する。`TraderAgent.decide` はFunction Callを解析し、`ReviewerAgent.review` は検証済みStrategy Patchを返す。`run_once` はCloud Run Jobからも呼べる一シフト関数とする。

必須依存はPython 3.12以上、`hyperliquid-python-sdk`、`google-genai`、`python-dotenv`、標準SQLiteである。HyperliquidとGeminiのSDKはアダプター境界の外へ型を漏らさない。

## 変更履歴

- 2026-08-22: ユーザー承認済み計画をPLANS.md形式へ展開して初版を作成した。
