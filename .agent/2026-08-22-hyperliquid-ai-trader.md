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
- [x] (2026-08-22 22:28+09:00) Gemini Trader/Reviewer、ローカルrunner、CLI、レポートをTDDで実装し、実GeminiのTrader/Reviewer単発検証に成功した。
- [x] (2026-08-22 22:43+09:00) 36 Trader・6 Reviewerの加速Dry-runを完走し、注文0件、Trader成功10件、rate limit例外26件、Reviewer成功1件、rate limit例外5件をSQLiteへ正確に保存した。
- [x] (2026-08-22 22:55+09:00) 開始済みCanaryをユーザー指示で停止した。第1シフトはGemini rate limitにより発注0件で、停止後のPreflightは建玉0・未約定注文0を確認した。
- [x] (2026-08-22 22:57+09:00) 実Canaryの完遂を待たず、ユーザーの明示指示により現在の実装をmainへ反映する。
- [ ] 三時間運転を完了し、匿名化要約をmainへ再pushする。
- [x] (2026-08-25) Testnetの残高不一致を調査し、Spot USDCとPerp証拠金の分離が根本原因であることを確認した。
- [x] (2026-08-25) Spot残高の取得、残高0時のpreflight停止、明示的なSpot→Perp移管CLIを追加した。自動移管は行わない。
- [x] (2026-08-25) Agent Walletでの移管試行が拒否されることを再現し、SDK送信前に権限不足を明示する検証を追加した。
- [x] (2026-08-25) Unified Accountを実口座で確認し、担保をspotClearinghouseStateから取得するよう修正した。
- [x] (2026-09-05) 実行レビューを反映し、`waitingForTrigger` 保護注文を受付済みとして扱うよう修正した。
- [x] (2026-09-05) 保護注文・部分約定・応答不明の再発注停止、未紐付け決済fillの候補限定、429同一シフト再試行停止、費用込みグループ損益を追加し、75テストを通過した。
- [x] (2026-09-06) 実Fillの `dir` と直前entry fillを使って未紐付け決済のシフト帰属を修正し、Gemini SDKの既定再試行を1回へ制限した。77テストを通過した。

## Surprises & Discoveries

- Observation: 現在のプロセスには `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` が `http://127.0.0.1:9` として注入されているが、User/Machine scopeには永続設定がない。
  Evidence: .NETのEnvironment APIでProcess/User/Machineを個別に確認した。ネットワークを使う子プロセスでは同じシェル内で三変数を解除する必要がある。
- Observation: Testnet口座は開始時点でequity 1000 USDC、使用証拠金0、建玉0である。
  Evidence: Proxyを使わない読み取り専用Info API呼び出しの `marginSummary` と `assetPositions` を確認した。
- Observation: 公式SDK 0.24.0は `bulk_orders` の `normalTpsl` grouping、IOC注文、trigger TP/SL、Client Order IDをサポートする。
  Evidence: 導入済みSDKのシグネチャと公式 `examples/basic_tpsl.py` を確認した。
- Observation: `.env` の `GEMINI_MODEL=gemini-3.6-flash` は実際に利用可能で、Trader Function CallingとReviewer Structured Outputの双方に成功した。
  Evidence: モデル利用可能性preflight、単発Trader、証拠ゼロのReviewer Patchを同じモデル名で実行した。秘密値は出力していない。
- Observation: 36シフトを待ち時間なしでGemini無料枠へ集中させると短時間quotaに達する。
  Evidence: Dry-run `dry-run-20260822T223424-df0b1141` は10 simulated、26 `rate_limited`、Reviewer 1 accepted、5 `rate_limited` で完了し、固定判断へフォールバックしなかった。
- Observation: Canary第1シフトもGeminiの生成時点でrate limitとなり、発注は作られなかった。ユーザーはこの既存実行の停止とmain反映を指示した。
  Evidence: `canary-20260822T225024-937f1719` のslot 0は `mandatory_entry_exception/rate_limited`、ordersは0件。停止後の実Preflightは `account_clean=true`。
- Observation: 現在の設定は親口座とAgent Walletの正しい組み合わせで、認可も成立している。一方、Perp口座は0 USDCで、Spot口座に2689.31777457 USDCが存在する。
  Evidence: Agent署名者は `role=agent` かつ `data.user` が `HL_test_wallet` と一致。Info APIの `user_state` は `accountValue=0`、`withdrawable=0`、`assetPositions=[]`、`frontend_open_orders=[]`。`spot_user_state` はUSDC total=2689.31777457を返した。
- Observation: Spot→Perp内部移管はAgent Walletでは許可されない。
  Evidence: 公式Python SDKの `basic_send_asset.py` は `exchange.account_address != exchange.wallet.address` の場合にAgentはinternal transfer不可として停止する。実際のCLI試行後もSDK送信前に同じ条件で拒否し、口座はSpot=2689.31777457、Perp=0のまま。
- Observation: 対象Testnet口座はUnified Accountであるため、Spot USDCがPerp担保として共有される。
  Evidence: `query_user_abstraction_state` は `unifiedAccount`、`spot_user_state` はUSDC total=2689.31777457、tokenToAvailableAfterMaintenance[USDC]=2689.31777457、Perp `user_state` はaccountValue=0を返した。公式ドキュメントもUnified/Portfolio Marginではspot clearinghouse stateを残高・holdの情報源と説明している。
- Observation: HyperliquidのBracket注文レスポンスでは、未発火のTP/SLが `waitingForTrigger` 文字列で返る。これを `resting` だけで判定すると、正常な保護注文を失敗扱いする。
  Evidence: 公式Exchange APIの注文ステータス仕様と、実行時の19シフト連続 `protection_rejected`、保存されたTP/SL `unknown` を照合した。
- Observation: 429を同一シフト内で再試行すると、無料枠の残りをさらに消費し、1時間運転の判断サンプルを減らす。
  Evidence: 直前の36シフトで17件の `rate_limited` が発生し、今回の修正では429を即時記録して次シフトへ進むテストを追加した。
- Observation: `userFillsByTime` の決済fillには `dir` と `startPosition` があり、時系列上の直前entry fillでシフトを特定できる。
  Evidence: 前回1時間RunのTestnet履歴で、short entry→short close→short entry→short close→long entry→long closeの順序を確認した。従来の「最新ordered cycle」推定ではcloseを隣のslotへ誤帰属していた。
- Observation: 現在のgoogle-genai SDKはHTTP retry未指定時に初回を含む最大5試行を設定する。
  Evidence: 専用venvの`google.genai._api_client`既定値と`HttpRetryOptions(attempts=1)`の動作を確認した。
- Observation: Gemini 3.6 Flashは公式モデル一覧でStableかつFunction Calling/Structured Outputs対応、料金表のStandard Free Tierでは入力・出力・キャッシュが無料。ただしRPM/TPM/RPDはプロジェクト・モデル依存で固定値ではない。
  Evidence: 2026-09-06に公式モデル仕様、料金表、レート制限、Billing FAQを確認。実際のquotaはAI Studio Dashboardで確認する必要がある。

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
- Decision: Spot→Perp移管は自動化せず、金額必須の明示CLIとして提供する。
  Rationale: Spot資金をPerp証拠金へ移すのは外部状態を変更するため、誤移管を避けつつ、残高0をpreflightで見逃さないため。
  Date/Author: 2026-08-25 / Codex
- Decision: Unified Accountでは移管せず、Spot ClearinghouseのUSDCをequity/available collateralに使う。
  Rationale: Unified AccountはSpotとPerpでUSDC担保を共有する仕様であり、旧Standard Account向け移管を行うと誤った外部状態変更になるため。
  Date/Author: 2026-08-25 / Codex
- Decision: `waitingForTrigger` と `waitingForFill` は保護注文受付済みとして扱い、保護注文系の確定失敗後は同一Runの新規発注を停止する。
  Rationale: 受付待ち状態を拒否と誤認せず、同一執行障害による連続発注と手数料損失を防ぐため。
  Date/Author: 2026-09-05 / Codex
- Decision: confidence・strategy・would_abstain別の `net_closed_pnl` はfill手数料を差し引いた取引単位で集計する。
  Rationale: 直前レポートのグループ損益が手数料を含まず、全体Net PnLと比較できなかったため。
  Date/Author: 2026-09-05 / Codex
- Decision: 未紐付け決済fillは、方向が分かる場合は同方向の未決済entry fillを時刻順に照合し、entry情報が無い場合だけ従来のcycle候補へfallbackする。
  Rationale: 前回Runで隣接slotへの誤帰属を再現し、DBスキーマを増やさずに実約定順序を使える最小修正とした。
  Date/Author: 2026-09-06 / Codex
- Decision: google-genai SDKのHTTP retry attemptsを1に固定する。
  Rationale: 無料枠の429に対するSDK内部の暗黙再試行を止め、外側のRun記録と1リクエスト1試行の挙動を一致させるため。
  Date/Author: 2026-09-06 / Codex

## Outcomes & Retrospective

レビュー修正の単体検証は完了した（77 passed）。前回1時間Runの実Fill時系列を再照合し、close帰属の回帰テストを追加した。次回Testnet運転では429の暗黙再試行がなく、確定fillの方向・entry順序に基づくレポートを確認する。

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
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file .env preflight
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file .env dry-run --cycles 36 --interval-seconds 0
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file .env canary --cycles 2
    hyperliquid-ai-trader\.venv\Scripts\python.exe -m hyperliquid_ai_trader.cli --env-file .env run-local --cycles 36 --interval-seconds 300

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
- 2026-08-22: 実Gemini検証と加速Dry-runの実績、短時間quotaの観測を追記した。
- 2026-08-22: CanaryはGemini quotaにより実注文前に停止したため、三時間テストは手動実行手順のみを引き渡す方針へ変更した。
- 2026-09-06: 前回Runのclose fill誤帰属をentry時系列照合へ修正し、Gemini SDKの暗黙retryを無効化した。公式Free Tierの料金・quota条件をREADMEへ追記した。
