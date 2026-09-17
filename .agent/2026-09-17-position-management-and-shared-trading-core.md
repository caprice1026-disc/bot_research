# 5分ごとのLLMポジション管理と共通取引基盤の実装方針

本書はリポジトリルートの `PLANS.md` に従う日本語ExecPlanである。実装時には Progress、Surprises & Discoveries、Decision Log、Outcomes & Retrospective を更新する。2026-09-17の実装では、外部API・課金実験・発注を行わず、共有coreとオフラインの継続ポジション管理fixtureまでを実装した。残る移設・Gemini接続・実取引はProgressに明記する。

## Purpose / Big Picture


5分を強制決済期限ではなく、市況と現在のポジションを再判断する周期にする。LLMはポジションがなければ新規建てか見送り、保有中なら維持・追加・縮小・全決済を選べる。5分経過だけでは売買せず、変化させる数量だけに執行費用を課す。人が観察できる完成例は「10:00にLONG、10:05は維持して注文ゼロ、10:10に追加、10:15に一部決済、10:20に全決済」であり、その全判断・Risk判定・約定・費用が保存されることである。

同時に、BTC過去データ取得から正規化、時刻整合、費用、数量制約、執行、口座台帳までを `trading-core/` へ集中させる。実験ディレクトリは仮説・prompt・実験条件・候補選定・分析・報告を担当する。共有基盤は一つのローカルPythonパッケージとし、別サービス、ジョブ基盤、汎用プラグインフレームワークにはしない。

基盤を移すだけの段階と売買挙動を変える段階を分離する。既存の5分保有結果や不採用結果を新方式の結果として読み替えず、旧CLI・旧入力・旧成果物を再現できる状態を維持する。今回の研究対象はBTCの単一銘柄・単一純建玉であり、複数銘柄、両建て、Mainnet実注文、Reviewerによる自動strategy更新は初期実装に含めない。

## Progress


- [x] (2026-09-17) 現行の研究設定、Batch経路、独立episode Simulator、既存取引adapter、データ取得元、既存計画を調査した。
- [x] (2026-09-17) 現状の全Hyperliquidテスト185件成功を確認した。一時ディレクトリACLを避ける実行権限で実施した。
- [x] (2026-09-17) 共通基盤移設と継続ポジション管理を一つの段階的な実装方針として記述した。
- [x] (2026-09-17) M0：既存Hyperliquidテスト185件を通常権限の専用一時領域で再実行し、既存のCLI、artifact、独立episode Simulatorの互換基準を固定した。
- [ ] M1（部分完了）：`trading-core/` package、内容SHA-256付きdataset catalogと連続性検証を追加した。既存BTC downloader、Funding reader、normalizer、旧CLIを同じ実装へ委譲する移設は未完了である。
- [ ] M2（部分完了）：新方式用のDecimal execution-cost、accounting、position-delta基盤を共有側へ置いた。既存Testnet adapter、旧Risk、旧Simulator、provider送信の移設と互換wrapperは未完了である。
- [x] (2026-09-17) M3：厳格な`target_position` schema、固定anchorによる一回限りの数量化、差分計画、反転拒否、stop・notional・stop-risk制約を実装した。
- [x] (2026-09-17) M4：open/hold/add/reduce/close、SL gap、Funding、保持上限、終端未決済を扱う一建玉Simulatorを実装した。
- [ ] M5（部分完了）：scripted policyだけを使う逐次runner、重複slot防止、60秒stale判定、3連続無効応答の安全決済、モデル費用上限、SQLite WAL run DBによる再起動照合、原子的なoffline成果物を実装した。Gemini通常APIと実取引adapterは未実装である。
- [ ] M6（部分完了）：fixture replayのJSON/Markdown reportを実装した。no-trade・固定方針・LLM各群の公平な比較と設定freezeは未実装である。
- [ ] M7：将来データのshadow運転を実施し、別途承認した場合だけTestnetへ接続する。

## Context and Orientation


調査時のHEADは `0b5fdc9`。作業ツリーには条件付き戦略の診断・採用判定修正、Gemini Batch adapter、Pilot設定とテストがある。これらもユーザーが依頼した「現状」の保存対象である。実装開始時は本書を含めてpushしたcommitを基準に取り直す。

`hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/` はデータ、特徴量、Simulator、Batch、候補研究を同居させている。`research/preparation.py` は `open_position` 用の独立requestを作り、`research/gemini_batch.py` はそのschemaをGeminiへ送る。`research/replay.py` と `research/conditional_replay.py` は完成episodeを計算する既存経路であり、途中の増減を表現する新しい口座Simulatorとは用途を分ける。

`hyperliquid-ai-trader/src/hyperliquid_ai_trader/exchange/`、`trading_tools.py`、`risk.py`、`storage.py`、`runner.py` は既存Testnetの取引基盤を含む。既存 `TradingExchange.place_bracket()` は新規建てと保護注文に焦点があり、任意の部分縮小・追加にそのまま安全に利用できるとは仮定しない。`hl_testnet_check/` は署名者・接続確認の互換CLIである。

BTC価格取得は `binance-btcusdt-futures-research/download_binance_klines.py`、追加市場入力は `collect_research_inputs.py` と `src/btc_regime_eda/collection.py` に分散している。同ディレクトリのHMMやEDA、walk-forward分析は実験固有であり共通基盤へ移さない。Polymarketや天候研究も今回の移設対象にしない。

既存の条件付き88候補は採用されず、`configs/research/initial_strategy.json` のv001はactive_rulesが空のままである。50地点Pilotも全見送りであり、見送りを含む応答動作の確認であって選別能力の証明ではない。Pilot設定 `llm_pilot_v001_gemini_35_flash_minimal.json` は最大保有300,000msだった。新方式はこの研究条件を変更するため別experiment_id、別prompt版、別schema版を使う。v001の仮説は初期入力として再利用できるが、旧実験の検証済み戦略とは呼ばない。

本書で「目標建玉」は判断完了後に持ちたい符号付きBTC数量、「純建玉」はLONGとSHORTを相殺した一つの数量、「差分注文」は目標数量と現在数量の差だけを執行する注文を指す。「shadow」は実注文を出さず、将来の公開市場情報を用いて仮想約定を記録する実験である。

## Decision Log


2026-09-17：共通基盤分離に賛成する。理由は同じBTCデータ、時刻、費用、Riskの重複実装を避け、実験間で比較条件を揃えられるためである。`trading-core/` を採用し、独立サービス化や全リポジトリの一括再編はしない。

2026-09-17：実験ごとに専用のLLM判断・戦略コードを残す。共有側は実験パッケージをimportしない。実験の仮説が変わるたびに共通Riskや取引所adapterが変わる依存方向を禁止する。

2026-09-17：初回は目標比率を離散値にする。任意の注文列や自由なレバレッジ変更より、実験の解釈・数量丸め・再送防止が明確になる。連続数量や指値戦略は、この比較で必要性が示された後の別実験とする。

2026-09-17：5分判断と保有期限を分離する。研究初期値の緊急保有上限は24時間とするが、これは利益最大化の設定ではなく無期限保有を避ける実験上の上限である。初回建て時刻から測り、追加・維持・再起動で延長しない。通常の決済はAI判断または保護条件で行い、24時間強制決済は別理由として集計する。

2026-09-17：新方式の最初の比較ではReviewerを無効とし、モデル、prompt、strategy、行動集合、Riskを凍結する。状態付き判断による効果とstrategy更新の効果を混ぜない。

2026-09-17：既存research moduleを直ちに共通coreへ移さない。新coreの契約をfixtureで先に実証し、旧CLIが`trading_core`のeditable installを暗黙に必要としない状態を維持する。旧経路をdelegationへ切り替えるのはM1/M2の残作業として、出力hash比較と同じcommitで行う。

2026-09-17：初回の逐次runnerはScripted Policyのみとする。これによりGemini API費用やTestnet注文なしで、重複slot、stale応答、費用上限、連続応答不能の安全動作を検証できる。通常Gemini API、SQLite再開、Testnet adapterはこのfixture条件と明示予算を満たす次段階まで接続しない。

## Surprises & Discoveries


既存実装の5分は単なる呼出間隔ではなかった。設定の `execution.max_hold_ms=300000` と `open_position` 専用入力が、保有中の継続判断を不可能にしていた。パラメータだけを24時間に変えても増減・口座状態入力・部分決済は実現しない。

Gemini Batchは今回すでに実装されている。過去の「adapter未実装」という説明は現状には適用しない。ただし独立50地点を一括送信する仕組みを、前の約定結果に依存する単一口座の将来判断へ流用できない。逐次判断を基本とし、Batchは独立した口座・実験間の同じステップを束ねる場合だけ将来の選択肢とする。

全体テストは今回 `185 passed in 3.02s`。これは既存経路の回帰確認であり、本書で提案する継続管理の実装済み証拠ではない。研究成果物、APIキー、raw応答、SQLite、仮想環境はGitに含めない。

2026-09-17：sandbox内でpytestが作成するbasetempは、実行終了時の走査で`WinError 5`になる。テスト本体の失敗ではなくACL境界であり、通常権限で`$env:TEMP`配下を指定すると既存185件と新規26件が正常終了した。以降はこのコマンドでコード不合格と環境ACLを区別する。

2026-09-17：初期実装中にDecimalの`copy_sign`を符号比較として使うと、絶対値も比較されて同方向の追加まで反転と誤判定した。`quantity > 0`の真偽値を比較する実装へ改め、追加・縮小・反転の受入テストで固定した。

## Architecture and Directory Boundaries


最終配置は以下とする。初期移設中は旧ディレクトリに互換wrapperを残す。実験ディレクトリの名前だけを変える作業は、本機能に必要ないため行わない。

    trading-core/
      pyproject.toml
      README.md
      src/trading_core/
        market_data/       # 公開取得、正規化、catalog、連続性、availability
        features/          # 共通の確定足特徴量
        execution/         # 差分注文、取引所adapter、保護と照合
        simulation/        # 旧episode simulator、新しい時系列口座simulator
        accounting/        # 建玉、fill、fee、Funding、equity、SQLite
        risk/              # 注文前・保有中の固定制限
        llm/               # provider送信、usage、request ID、応答保存
        artifacts/         # hash、manifest、freeze、データ参照解決
        cli.py             # data取得・検証・移行、接続診断
      tests/
      data/                # Git除外。共通のraw/normalized/catalog
    hyperliquid-ai-trader/
      configs/ prompts/    # 既存実験の固定資産
      src/hyperliquid_ai_trader/research/
                           # 候補定義、points、評価・採否・診断、旧実験CLI
      tests/ data/ reports/
    llm-position-management/
      pyproject.toml
      README.md
      configs/ prompts/
      src/llm_position_management/
        decisions.py       # 目標建玉のschemaと意味検証
        observations.py    # 市況・口座・判断履歴のLLM入力
        policy.py          # scripted/LLMの判断、strategy版固定
        runner.py          # 5分周期・逐次進行・再開
        report.py          # 比較指標、見送り・増減の分析
        cli.py
      tests/ data/ reports/
    binance-btcusdt-futures-research/
                           # EDA/HMM/分析を保持。取得コマンドは互換wrapper

`trading-core` のimport名は `trading_core` とする。共通部分は標準ライブラリを優先し、`google-genai`、Hyperliquid SDKなどは必要なextraに分ける。EDA用のpandas、hmmlearn、描画ライブラリを取引基盤の必須依存へ引き上げない。Python下限は共通部分3.12、既存EDAは現行3.13を維持する。初期開発は既存 `.venv` に共通パッケージをeditable installし、別環境でのwheel installもM2で確認する。`sys.path` の場当たり的な書換えで依存を解決しない。

移設対応は `research/data.py`・`collector.py`・`binance.py` を `market_data/`、共通feature計算を `features/`、`costs.py`・既存 `simulator.py` を `simulation/`、`exchange/`・SDK gateway・機械的注文処理を `execution/`、共通口座型と台帳を `accounting/` とする。live Riskと研究Riskは最初は別クラスのまま移し、意味の違う制約を移設時に一つへ統合しない。`research/batch.py`・`request_identity.py`・provider送信処理を `llm/` へ移す一方、旧 `open_position` 宣言や新 `target_position` 宣言は実験から引数として渡す。現 `gemini_batch.py` のhardcode schemaは互換adapterを通して抽出する。

`agents.py`、`runner.py`、`storage.py` のような共通機構と実験方針が混在するファイルは丸ごと移さない。schema解釈、Reviewer方針、旧schedulerは実験側、fill記録・注文照合は共通側へ分ける。共通側のデフォルトprompt探索が `hyperliquid-ai-trader/prompts/` を参照する設計を残さず、呼出側が資産パスとhashを渡す。

## Shared Data Ownership and Migration


価格とFundingは `trading-core/data/raw/<venue>/<symbol>/<kind>/<range>/`、正規化済み入力は `trading-core/data/normalized/<dataset_id>/`、索引は `trading-core/data/catalog.json` に置く。dataset_idはvenue、symbol、interval、UTC範囲、schema版、content hashに結び付ける。Binance USD-MとHyperliquid Mainnetを同一BTCとして上書きしない。

各実験は価格本体のコピーではなく `inputs.json` にdataset_id、期待SHA-256、要求期間、Funding参照を保存する。結果・モデル応答・判断・試行履歴は実験配下のrunに保存する。共通取得処理は実験のresultを更新せず、実験は確定データ本体を変更しない。追加期間の取得は新しい不変datasetを作り、既存freezeの内容を変えない。

取得処理は既存checksum照合・UTC期間終端除外・確定足・受信時刻・availabilityを維持する。既取得の成功ファイルはhashを検証して再利用し、未取得または不完全な日だけ再取得する。raw取得時刻と過去足に仮定したdelivery delayを区別する。Fundingの8時間境界への正規化と実欠損の拒否も移設する。

ローカルの大きなファイルを直ちに `Move-Item` しない。M1では新ルートへコピーし、元と先のSHA-256・件数・期間・manifest参照を確認し、全consumerを切り替えた後に旧コピーの削除を別操作とする。Windows上でコピー/移動対象の絶対パスがrepo配下に収まることを先に検証する。symlinkやjunctionを必須にしない。

既存freezeのpath/hashは監査証拠なので書き換えない。旧相対パスから新datasetへの対応を別migration manifestに記録し、resolverが旧参照とhashを確認して解決する。移行前の旧コマンドもwrapper経由で動くことを確認する。`.env` と秘密鍵は移動・複製せず、実行時の明示パスまたは環境変数で読む。live DBと研究DBを共有しない。

## Position Decision Contract


LLMから共通執行へ直接注文を送らない。一回の応答に一つの `target_position` 提案を要求し、実験側parserで厳密検証してからRiskへ渡す。schema版は旧 `open_position` と別にする。最小応答例は以下である。

    {
      "schema_version": 1,
      "decision_id": "run-id:utc-slot",
      "intent": "set_target",
      "target_fraction": "0.50",
      "stop_price": "60000.0",
      "thesis": "観測特徴量に基づく保有理由",
      "invalidation": "次回に再確認する撤退条件"
    }

`intent` は `hold` または `set_target`。`hold` ではtarget_fractionとstop_priceをnullとし、現在数量・保護注文をそのまま保つ。flatのholdは見送り、建玉ありのholdは維持である。`set_target` は `-1,-0.5,-0.25,0,0.25,0.5,1` のみを受け付け、0は全決済。bool、NaN、Infinity、未知key、複数call、ID不一致を拒否する。thesisとinvalidationは各500文字までに制限し、次回入力の情報量を固定する。confidenceは新規schemaから外す。動的な保有方針に旧「5分net利益確率」を流用しない。

比率の分母はrun開始時に固定した `exposure_anchor_usd` とする。研究初期値250 USDは旧参照額との比較用であり、推奨資金量ではない。時価やequityが変わるたびに分母を変更しない。`set_target` の新判断だけ、decision snapshotのmarkでBTC目標数量を確定し、銘柄の数量刻みに向かって絶対値を切り下げる。その確定数量をdecision IDへ永続化し、再送時には現在価格で再計算しない。`hold` は数量を再調整しないため、値動きだけで売買を繰り返さない。

目標数量と実数量の差が数量刻み未満、または差分notionalが最小注文未満なら `no_op_dust` とする。全決済時の端数量は取引所のreduce-only制約と最小注文例外を確認し、残量を0と偽らず `residual_position` で照合する。市場・レバレッジ・wallet・Risk制限はLLMが指定できない。

初期版では建玉を持ったまま反対符号の目標へ直接移れない。`reversal_requires_flat` として拒否し、LLMは一度0で全決済し、次の判断slotから反対方向へ建てる。往復を一つの注文へ隠さない。追加は同方向のみ。増加後の平均価格と全建玉のstop riskを再計算し、Riskを超えれば要求全体を拒否する。無言で上限へ丸めて「AIの判断どおり」と記録しない。

## Risk and Protection


新experiment設定に最大notional、leverage、最小notional、数量刻み、1ポジションstop risk、日次損失、DD、保有上限、鮮度上限を明示する。既存設定の数値を黙って緩めない。simulator上の既定比較値は初期equity1000、notional上限250、risk_per_trade_pct=1、日次損失20%、DD25%、leverage5、min_notional10を旧値から引き継ぐ。これらは研究の比較条件であり、Testnet/liveへそのまま推奨する値ではない。

追加後のworst-case stop損失は全保有数量×markからstopまでの不利幅に決済費用を加えて評価し、現時点のequityに対する許容量を確認する。既存取得原価に対する最終損益も別に保存する。資金余力、日次制限、DDには現在の含み損益を含める。縮小・決済を「新規取引制限に達したから」という理由で禁止しない。強制制限到達時は追加禁止だけでなく安全決済を開始し、確認できるまで停止状態にする。

LONG stopは現在価格未満、SHORT stopは現在価格超とする。新規・追加には有効stopを必須とし、既存stopの緩和は禁止する。holdでは変更せず、stopのみを締める操作は初期版に入れない。部分縮小では残量に対応するreduce-only stopへ更新する。固定TPは新方式の初期版では使わず、AIの縮小・決済とstopで出口を構成する。旧方式との比較でTPの有無が変わる点は明示する。

5分の間にもstop、Funding、最大保有、強制Riskを処理する。AIの遅延・無効応答は即時の全決済理由にはせず、有効な保護を維持して `decision_unavailable` とする。ただし連続3slotで有効判断が得られない場合は新規追加を止め、安全決済へ移る実験ルールとする。保護の存在を確認できない、口座と台帳が一致しない、取引所応答が不明な場合は通常判断を停止して照合する。安全動作を実行できなければ `reconciliation_required` を永続化し、成功扱いしない。

## Time, Simulation and Accounting


1分足で内部時刻を進め、5分境界だけLLM判断を生成する。判断tに利用するのはavailable_at <= tの確定足と、その時刻までに約定・通知が確定した口座状態だけである。t以後のSL到達や終値、将来の資金調達率は入力へ含めない。

同一時刻の順序は、直前区間の既存stop/決済、当該時刻のFunding（直前から保有している数量へ適用）、口座mark更新、UTC日次境界更新、Risk確認、判断snapshot確定、AI応答待ち、受信時点の再照合、注文計画確定とする。Funding境界で新規に作る建玉は境界前保有として扱わない。基準はconfigへ記録し、取引所別の仕様差はadapterに閉じ込める。

offlineではモデル遅延を固定設定としてシミュレーションし、最初の執行可能な次の1分足始値以降で約定する。shadowでは実際の受信時刻と市場の次の観測を使い、過去の始値で約定させない。実APIの壁時計時刻とReplayの市場時刻を別フィールドに保存する。未来のepisodeを先に完成させてそのPnLを次の判断へ渡す実装は禁止する。

新口座は signed_quantity、average_entry_price、realized_pnl、unrealized_pnl、cash、fees、funding、equity、peak_equity、day_start_equity、position_opened_at、stop_price を持つ。同方向追加時は数量加重平均価格に更新する。縮小時は縮小量だけの価格差損益を確定し、残量の平均価格は変えない。全決済時にposition lifecycleを閉じる。追加してもopened_atは更新しない。SL後はflatとなり、古いposition IDに対する遅い追加指示は破棄する。

各fillにpre-cost価格、spread調整、slippage調整、実行価格、数量、feeを保存する。調整済み価格から計算したPnLにspread/slippageを再控除しない。Fundingはイベント時点の符号付き建玉と定義したmark notionalへ適用し、途中の増減を反映する。holdはfeeゼロだが、Fundingとmarkによるequity変動はあり得る。日次・月次の実現損益はexit時刻、Fundingはevent時刻へ帰属させる。

stopが足内で到達した場合は保守的な約定規則を使う。gapではstop価格より不利な始値と費用で執行する。既存保護と新規指示が競合する足は既存保護を優先し、古いsnapshotに基づく指示は執行しない。1分OHLCVでは経路順を特定できないケースを `ambiguous_intrabar` として残し、実約定精度を主張しない。

価格/Funding欠損で口座が未確定になった場合は以降の確定成績を生成せずpartialにする。データ終端は `end_of_data` とし、勝手に最後の価格で決済したことにしない。終了時建玉、未実現PnL、評価価格時刻を保存する。終端強制決済を比較したい場合は事前設定された別シナリオとする。含み損込みDDと決済済みDDを両方報告し、現段階のmargin確認を清算エンジンの再現と呼ばない。

## LLM Observation and Sequential Runner


LLM入力は market、position、account_limits、last_decision、allowed_actions、execution_costs の固定構造にする。marketは既存common_candles_v1の確定特徴量を利用し、データ窓は少なくとも60分を確保する。5分判断だから5分returnだけを見る構成にはしない。positionは現在数量、side、平均取得価格、mark、保有時間、stop、含み損益。account_limitsは追加可能額、許容残余risk、停止理由。last_decisionは直前の有効判断と短い保有理由・撤退条件のみとし、無制限に対話履歴を伸ばさない。

口座入力に秘密鍵、wallet、API keyを含めない。利益回復や損失取り返しを目的に数量を増やすようなinstructionを入れない。元のconstitutionは口座入力を禁止しているためコピーして黙って変更せず、新実験専用 `constitution_position_v001.md` と `manager_v001.md` を作る。v001 strategyのhashと新promptのhashを両方freezeする。

`decision_id = run_id + market_slot` を主キーにし、request hashへschema・model・生成設定・prompt・strategy・market snapshot・position version・Risk configを含める。返却されたrequest/model情報も保存し、要求モデル名を返却モデル名と偽って補完しない。SDKが返却model版を提供しないときはunknownと明記する。モデル変更やfallbackを同じrunへ混ぜない。

一つの口座内は逐次実行する。次のslotの入力を作る前に、前のfillと市場イベントを反映する。AI呼出中も市場監視と保護照合を止めない。snapshot作成から60秒を超えた応答、position versionの変わった応答はstaleとして注文に使わず、次slotで再判断する。過去slotを追いかけて連続発注しない。並列化は独立run間に限定する。

API呼出前に入力・最大出力・thinkingを含む費用上限を予約する。単価・料金区分・計測日・推定/確定の区別をrunへ保存し、既存の出力tokenのみの定数予約を完全な予算上限とみなさない。無効応答・失敗jobの既発生費用も消さない。429、接続喪失、SDK固有の一時障害はprovider adapterで再試行可能状態へ分類し、providerが失敗を確定するまで予約を解放しない。外部送信済みか不明な要求は自動再送しない。

24時間では5分間隔で288判断となる。実行前に最大呼出回数と予算を設定し、超過前にモデル呼出を停止する。課金pilotとshadowは明示された予算内でのみ実行する。本書作成は新しい課金実験の承認ではない。

## Persistence, Execution and Recovery


SQLiteを継続使用し、単一口座に単一writerとする。新run DBにはruns、observations、decisions、order_intents、fills、funding_events、position_snapshots、risk_events、api_usageを保存する。旧live DBを新schemaでそのまま開かない。schema migrationは版付きで、未知の上位版は拒否する。

decisionはprepared、response_saved、validated/rejected/staleを経由する。order intentはplanned、submitting、acknowledged、partially_filled/filled、cancelled、submission_unknown、reconciliation_requiredを区別する。注文前にrequest IDと注文intentをcommitし、取引所が返すfill IDにunique制約を付ける。同じrequestの再開は新注文生成ではなく照合から始める。

目標数量の永続化だけでは重複注文を防げない。送信後の応答喪失では、未約定注文と実fillをclient order IDで照会し、実数量を確定するまで同じ差分を再送しない。部分約定した場合は実数量を記録し、残りを自動で何度も追いかけない。次slotで新しい有効判断を要求する。

Testnet adapterでの増減は、実数量・保護数量・client order IDを照合する。旧stopを無条件に先に取消して無保護にしない。取引所がatomicな保護更新を提供しない場合の手順はSDKとsandboxで確認し、追加部分を保護できなければ追加分を解消し、残量も守れなければ全決済へ移る。縮小はreduce-onlyとし、fill後に残量の保護を照合する。起動時の他システム建玉は所有権不明として停止し、勝手に取り込んだり閉じたりしない。

既存 `TradingTools.open_position()` の内部から新runnerを呼ばない。新しい `plan_position_delta()` と実行adapterを共通側に用意し、旧新の経路は同じ機械的約定・照合部品を使用する。現行Mainnet拒否を維持する。

## Interfaces and Dependencies


型はDecimalとUTC epoch millisecondsを使うdataclassとする。金額・数量のJSON表現はdecimal文字列、時刻は整数とし、floatの暗黙変換を避ける。次の名前を実装境界として用いる。

    # trading_core.market_data.catalog
    resolve_dataset(dataset_id: str, expected_sha256: str) -> DatasetRef
    verify_dataset(ref: DatasetRef) -> CoverageReport
    # DatasetRef: path, venue, symbol, interval_ms, start_ms, end_ms, sha256
    # CoverageReport: status, requested_range, observed_range, gaps, funding_gaps

    # llm_position_management.decisions / observations
    parse_target_decision(payload: dict, context: DecisionContext) -> TargetDecision
    build_observation(snapshot: AccountSnapshot, market: MarketSnapshot,
                      previous: DecisionRecord | None, limits: RiskLimits) -> dict
    # TargetDecision: decision_id, intent, target_fraction, stop_price,
    #                 thesis, invalidation, source_position_version

    # trading_core.execution.position_plan
    plan_position_delta(decision: PositionTarget, snapshot: AccountSnapshot,
                        market: MarketSnapshot, limits: RiskLimits) -> PlanResult
    # TargetDecisionを実験adapterで共通のPositionTargetへ変換する。
    # PositionTarget: decision_id, hold, frozen_target_quantity, stop_price,
    #                 source_position_version。計算済み数量を再送時に変えない。
    # PlanResult: status, reason, delta_quantity, reduce_only, target_quantity,
    #             protection_plan, expected_cost, intent_id

    # trading_core.simulation.position_account
    advance_to(timestamp_ms: int) -> tuple[AccountEvent, ...]
    apply_plan(plan: PlanResult, executable_at_ms: int) -> tuple[AccountEvent, ...]
    snapshot(timestamp_ms: int) -> AccountSnapshot
    # apply_planは将来fillを予約し、未来PnLを現在snapshotへ反映しない。

    # llm_position_management.policy
    decide(observation: dict, request_id: str) -> SavedModelResponse
    # scripted fixtureとGemini通常APIの両方がこの境界を満たす。

AccountSnapshotはposition_version、signed_quantity、average_entry_price、cash、equity、unrealized_pnl、opened_at、stop、pending_order_ids、risk_stateを持つ。MarketSnapshotはas_of、available_at、markと確定featuresを持つ。共通型は `trading_core/accounting/models.py` と `market_data/models.py` に置き、experiment側のclassを共有層からimportしない。

## Plan of Work


### M0：既存結果を互換性基準にする


旧CLIのhelp、設定検証、固定fixtureのbaseline・points・request hash・応答検証・診断出力を記録する。run時刻や絶対path以外のcanonical内容hashを比較基準にし、揺れる項目の除外を明記する。既存v001とfreezeを上書きしない。`tests/test_research_artifacts.py` と既存collector/Simulator testsを利用し、巨大な365日データをテストrepoへ追加しない。

### M1：取得とデータ参照を共通化する


`trading-core/pyproject.toml`、catalog、公開取得CLIを追加する。既存download script、Funding collector、normalizerを移し、旧entrypointは同じ引数を受けて共有関数へ委譲する。固定fixtureで取得checksum・Funding境界・market identity・確定足・manifest hashが変わらないことを検証する。ローカル実データはcopy-and-verifyで一つのdatasetとして登録し、manifest完成前のファイルは下流から見えないようにする。

### M2：執行基盤を移し、実験側の依存を一方向にする


上記の移設対応に従ってRisk、旧Simulator、台帳、SDK、provider送信部品を抽出する。移した関数には旧moduleからの薄いre-exportを一時的に残す。`pyproject.toml`、run.ps1、CLI asset探索、testsのimportを更新する。baselineと診断の固定fixtureが一致し、旧Testnet CLIのhelp/preflight用fakeが通ることを合格条件とする。依存循環と、共通側から実験名へのimportをテストで禁止する。

### M3：判断・差分計画を実装する


新実験のschema、新prompt、公開configと `plan_position_delta` を作る。まだLLM API・取引所へ接続せずscripted判断で数量を確認する。250 USD anchor、mark=50,000、target=0.5なら目標0.0025 BTC、現在0.001なら差分0.0015。holdなら価格が変わっても差分ゼロ、0なら全縮小、反転なら拒否であることを検証する。step sizeやRisk拒否理由まで保存する。

### M4：時系列口座を実装する


`trading_core/simulation/position_account.py` を追加し、1分刻みの市場イベントと5分判断を分離する。旧episode Simulatorは維持する。scriptedシナリオで追加・部分決済・SL・Funding・跨日・終端を検証し、flatからflatまでのlifecycleに複数fillが紐付くことを確認する。数量・cash・費用を手計算したfixtureと照合してから実データへ接続する。

### M5：逐次LLM runnerと再開を実装する


新 `policy.py`、`runner.py`、SQLite run状態を実装する。Gemini通常APIの送信では新しいschemaを注入し、SDK自動関数実行を無効のままにする。既存Pilotのminimal thinkingとtoken設定は初期接続参考値であり、増えた口座入力でも応答が収まるかcanaryで確認してから固定する。無効応答を0ポジションへ変換しない。fixtureでAPI遅延、SL競合、予算上限、再開を確認し、その後に予算を明示した少数canaryを行う。

### M6：公平な比較と報告を作る


同じデータ・費用・Riskで、no-trade、固定SLの単純ルール、LLMのopen/hold/close、LLMの増減許可を比較する。open/hold/close群はtargetを-1/0/1に制限し、他の条件を合わせる。旧5分保有Pilotは参考欄に分け、行動集合とexit条件が異なるものを純粋なモデル能力差と呼ばない。

全判断数、見送り・維持・追加・縮小・決済数、turnover、費用、保有時間分布、stop/AI/期限別決済、実現・未実現PnL、含み損込みDD、API費用、遅延・無効応答・stale率を報告する。平均取引損益の単位はflat-to-flat lifecycleとし、fillや部分決済の件数と分ける。品質statusと研究結論を別にし、全見送りをedge発見としない。

ポジション管理では見送り後の仮想ポジションが将来判断を変えるため、旧50地点shadow PnLの単純合計を対照にしない。対照は独立口座として最後までReplayする。評価区間・最小観測・候補数・予算・採否を実験前に記録し、不足はinconclusiveとする。最初は機能確認を目的とし、短いrunから収益性を認定しない。

### M7：future shadowからTestnetへ進める


公開データcollectorを共通基盤で継続稼働させ、固定設定で将来の判断を結果より先に保存する。最初の24時間は機能・欠損・費用の観測、その後の評価期間は開始前に別途固定する。Binance過去入力とHyperliquid将来入力のvenue差を明示し、live対象がHyperliquidなら同venueのshadowを必須にする。Testnetは新規/維持/追加/縮小/全決済/SLの実約定と照合を検証する工程であり、収益性の検証と区別する。

## Concrete Steps


以下は実装済みまたは将来実装するCLI契約である。作業ディレクトリはrepository root。M1/M2では既存環境を使ってlocal packageをinstallする。

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pip install -e ".\trading-core"
    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pip install -e ".\llm-position-management[test]"
    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m trading_core.cli data verify --catalog trading-core\data\catalog.json --inputs llm-position-management\configs\inputs.json
    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.cli validate-config --config llm-position-management\configs\pilot.json
    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m llm_position_management.cli replay --config llm-position-management\configs\fixture.json --output llm-position-management\data\fixture-run

`inputs.json` はM1で登録した実在dataset参照を生成して使う。`fixture.json` はテストの10:00〜10:25市場データとscripted判断列を参照する公開configとし、外部APIなしで最後まで動く。`pilot.json` はallow_paid_api=falseを初期値とし、CLIの実行だけで課金を有効にしない。

2026-09-17にfixture replayを実行した。`decision_count=5`、`fill_count=4`、`hold=1`、最終signed quantity=0であり、`run_manifest.json`は`status=complete`、SQLiteの`journal_mode`は`wal`、decision rowsは5だった。これはfees=0の動作fixtureであり、収益性の結果ではない。

    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pytest trading-core\tests -q --basetemp $env:TEMP\hl-core -p no:cacheprovider
    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pytest llm-position-management\tests -q --basetemp $env:TEMP\hl-position -p no:cacheprovider
    .\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pytest hyperliquid-ai-trader\tests -q --basetemp $env:TEMP\hl-legacy -p no:cacheprovider
    git diff --check

EDA側の依存を含む既存環境で `binance-btcusdt-futures-research/tests` も別途実行する。環境の存在を確認し、依存が無いときに他projectへ暗黙installしない。Windows ACLで失敗した場合は新しい専用basetempで実行し、権限問題とテスト不合格を区別する。

将来の `shadow --config ... --duration-hours 24 --output ...` は公開データとGeminiを使うため、offline fixture合格と明示予算を条件にする。`testnet --config ...` は別modeとし、shadowから自動昇格しない。`--resume <run-dir>` はhash・model・schemaが一致する場合だけ続行し、設定変更時は新runにする。

## Validation and Acceptance


共通移設の受入では旧import/CLIと新import/CLIの出力を固定fixtureで一致させる。raw/normalized/Fundingのhash、時刻範囲、件数を照合し、データが実験ごとに別取得されないことを確認する。旧freezeはresolverで検証可能でなければ移行完了としない。

新機能の必須テストは `test_position_plan.py` にhold時のゼロ注文、固定anchor、targetの一回だけの数量化、反転拒否、最小数量、縮小許可、追加Risk拒否を置く。`test_position_account.py` に平均価格、部分損益、SL gap、Funding境界、UTC跨日、含みDD、費用の二重控除防止、保有上限非延長、終端未決済を置く。`test_position_runner.py` に重複slot、応答不明、再起動、部分fill、stale応答、AI待機中のSL、3連続失敗、予算上限、未来データ非参照を置く。

時系列fixtureでは10:00にopen、10:05にhold、10:10にincrease、10:15にreduce、10:20にcloseを入力し、5判断に対し売買intentは4件、holdのfeeはゼロ、最後はflatとなることを確認する。別fixtureは10:03にSL到達とし、10:05のsnapshotがflatであること、10:00の応答が遅れて10:04に到着したら古いpositionを復活させないことを確認する。

未来足や未来Fundingを改変しても時刻tの観測・判断入力hashが不変であることを検証する。取得価格50,000で0.002 BTC、52,000で0.001 BTC追加なら平均価格50,666.666...となり、51,000で0.001 BTC縮小時のpre-fee価格差損益は約0.333333 USD、残量0.002 BTCの平均価格は不変になる。decimal精度と丸め規則をfixtureで固定する。

再送テストでは注文受付直後に応答を落とし、再起動後に既存client order IDを照合してfill数が増えないことを確認する。保護更新失敗のfakeでは追加分縮小または全決済が記録され、未保護のまま通常runへ復帰しないことを確認する。実ネットワークを使わず再現できる状態がM5の完了条件である。

収益性の採用は機能受入から分離する。損失や全見送りでも処理は正常完了し得る。データ不足、ゼロ取引、DD停止を利益優位と認定しない。今後の採用基準は検証期間の口座結果を使い、探索期に止まった年間口座と検証期の成績を混同しない。

## Idempotence and Recovery


移設はM1/M2を独立commitとし、売買挙動変更を混ぜない。実験からの互換importとwrapperは同等出力確認まで保持し、削除は別commitにする。失敗時はコードを戻して旧データ参照で再現できるよう、元のraw/normalizedと旧DBを残す。大きな実データや古いrunの削除を移設成功条件にしない。

成果物はrun単位の一時pathから原子的に確定し、complete manifestがなければ下流で拒否する。既存完了runへの上書きは拒否する。中断runの再開はfill/decision/usageの永続状態を照合し、確定済みイベントを二重適用しない。外部注文の不明状態は照合できるまで停止する。DBと応答ファイル間の中断に備え、raw応答の永続保存後にcompletedを記録し、DBにはhashとpathを保持する。

## Artifacts and Notes


新runの最低成果物はrun_manifest.json、inputs.json、observations.jsonl、requests.jsonl、responses.jsonl、decisions.jsonl、fills.jsonl、funding.jsonl、equity.jsonl、risk_events.jsonl、usage.json、report.json、report.md、run.dbとする。run_manifestにcode SHA、dirty tree拒否、package版、schema、model、prompt、strategy、Risk、データ、費用、判断周期、保有上限、予算、比較群を固定する。読み手が同じ入力へ辿れないrunは再現可能と呼ばない。

LLMの理由文は観測根拠であり事実の証明ではない。引用したfeatureと実入力の整合性を確認するが、理由の流暢さを収益評価に加点しない。取引しないbaseline、固定方針、増減方針を同じreportに並べ、自由度を増やすことでturnoverと費用が増えた場合もそのまま示す。

## Outcomes & Retrospective


2026-09-17時点で、新`trading-core/`と`llm-position-management/`の最小実装を追加した。`trading-core`はhash固定dataset catalog、差分注文計画、Decimal cost、時系列一建玉Simulatorを持つ。`llm-position-management`はstrict parser、scripted sequential runner、SQLite WALのrecovery、原子的なoffline replay成果物を持つ。M3/M4は受入テストで完了したが、M1/M2の旧経路移設、M5のGemini・実取引照合、M6の比較研究、M7のshadow/Testnetは未完了である。ディレクトリ移動、データ再取得、新しいモデル呼出、注文は行っていない。

変更履歴：2026-09-17 初版。5分を観測周期とするユーザーの意図、継続ポジション管理、共通データ・取引基盤への集中を反映した。過去の独立5分episode実験は履歴として維持する。2026-09-17 実装更新。互換基準、new coreのM3/M4、Scripted-only M5、offline report、ACL回避手順と未完了範囲を反映した。
