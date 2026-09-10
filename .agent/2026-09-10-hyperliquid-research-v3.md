# Hyperliquid研究v3：レビュー結果と具体的な実装方針

このExecPlanはリポジトリルートの`PLANS.md`に従う。実装時にProgress、Surprises & Discoveries、Decision Log、Outcomes & Retrospectiveを更新する。2026-09-10時点では文書レビューと実装方針の作成だけを行った。以下に示す新規モジュール・CLIは実装予定であり、現在使える機能ではない。

## Purpose / Big Picture


同じ確定市場データと費用条件で、単純ルール、固定strategyのTrader、日次Reviewerでstrategyを更新するTraderを比較できる研究経路を作る。判断の良さ、資金管理、実注文の正しさを別々に測り、見送りの仮想利益を口座利益へ混ぜずに学習材料として使う。

レビュー対象は`hyperliquid-ai-trader/housinv3.md`。依頼にあった`nousinv3.md`は存在せず、同名に近いv3文書を対象とした。元文書は保存する。既存の`.agent/2026-08-22-hyperliquid-ai-trader.md`はTestnet Bot v0.1の履歴として残し、本計画は新しい研究経路に限定する。

結論は「研究方針は採用可能。ただし、以下の仕様補完を反映したうえで実装する」。Binanceで初期仮説を作り、HyperliquidのDevelopmentで候補を絞り、構成を固定して将来比較する順序は妥当である。文書の費用試算も、示されたtoken仮定では整合する。収益性や必要件数を確認済みという判断ではない。

## Progress


- [x] (2026-09-10) v3文書を通読し、現行の特徴量、Runner、証拠保存、Risk、Strategy、Gemini経路と照合した。
- [x] (2026-09-10) 公式のモデル仕様、料金、Batch、足取得上限を確認し、実装前に補う仕様を整理した。
- [x] (2026-09-10) 本実装方針を作成した。コード変更・API課金実験・新規取引は行っていない。
- [x] (2026-09-10) リモートmainのM1/M2実装をfast-forwardで取り込み、到着猶予、1分足保有期限、数量flat判定、Reviewer証拠の実験・cutoff境界を回帰テストで補正した（101件通過）。
- [x] (2026-09-10) M1の公開足基盤として、署名なしHyperliquid Mainnet `Info` reader、確定1分足の正規化、原子的JSONL保存/再読込、既存Binance downloaderの1分足対応を追加した（Hyperliquid 104件、Binance 6件通過）。
- [ ] M1：時刻とデータ品質、共通特徴量、確定証拠の基盤（完了：時点整合の1分足モデル、連続性・OHLCV検証、`common_candles_v1`、研究専用SQLiteの確定episode・review証拠消費、公開足の正規化・JSONL保存。残り：取得CLI、source manifest、部分fillからepisodeへの集約）。
- [ ] M2：仮想執行、単純ルール、口座台帳（完了：Entry遅延、SL/TP、同足曖昧性、gap、300秒、費用、LONG/SHORT共通損益、3単純ルール、shadow非加算、UTC日次口座。残り：funding event、margin/liquidation、時系列runnerとの統合）。
- [ ] M3：Trader入力分離、Batch、回答保存と予算管理（完了：公開JSON設定、`validate-config` CLI、`allow_paid_api=false`・`budget_usd=0`の既定拒否。残り：prompt、request hash、Batch/usage/予約）。
- [ ] M4：点評価と3日Replay。
- [ ] M5：日次Reviewer、根拠検証、翌日strategy適用。
- [ ] M6：構成固定と将来Static/Adaptive比較。
- [ ] M7：Testnet注文監査と研究結果の引き渡し。

## Surprises & Discoveries


現行コードの調査基準はcommit `009eb6779c4569b2f518ace8b6e16614cdf65446`。作業開始時の`git status --short`は空だった。

`exchange/hyperliquid.py:get_market_observation`は`t`からCandleを作るが、足の終了時刻・利用可能時刻の保持や確定足の明示選別がない。`features.py:build_market_features`は板を必須としATRをmarkで割る。v3の共通OHLCV入力へそのまま流用できない。

`storage.py:recent_closed_trades`はgross PnLが0の群を除外し、最後の非ゼロ損益fillのIDを取引IDにする。損益ゼロの決済、分割決済、未帰属slot=-1の混合は、研究証拠としてそのまま採用できない。`new_closed_trades_since_review`は失敗レビューも含め全入力を既評価扱いするため、429等で評価されていない証拠も新規から消える。

`runner.py:review_once`は最大100件を入力に使い、その場でstrategyを保存する。`daily_realized_pnl`はRun全体の損益、`day_start_equity`は起動時の残高で、UTC日次リセットではない。`LocalRunner`は同期的にreviewを待ち、次シフト開始時にcleanupするため、v3の日次締切とEntry起点300秒の管理は別途必要である。

`storage.py:abstention_reference_outcomes`は次slotのmarkから推定往復費用を引く参考値であり、提案TP/SL、Entry遅延、fundingを再現しない。v3の共通Simulator結果と同じラベルで混在させない。

`strategy.py:_require_evidence`はID配列の型だけを検証する。実在ID、時刻、件数、日次変更量は未検証である。`RiskEngine.create_plan`はconfidenceで数量や見送りを変えない。

Binanceの既存`download_binance_klines.py`は1d/1h/15mだけを受け付ける。1m取得は単なる引数指定では動かず、対応を追加する必要がある。データ全期間の取得済み判定は本レビューではしていない。

M1の最初の実装では`NormalizedCandle`を新設し、`available_at_ms`で確定・受信済みの足だけを特徴量へ渡すようにした。2026-09-10の`tests/test_research_data.py`は、未来の足を変えても判断時点の特徴量が変わらないこと、未到着足・欠損・OHLC不整合を拒否することを確認し、全体で92件通過した。

続く実装で`research/simulator.py`と`research/store.py`を追加した。SimulatorはEntry起点の期限と費用を一箇所で計算し、同足両到達を`ambiguous_intrabar`として保持する。研究台帳はgross=0を含むflat episodeを残し、失敗reviewでは証拠を消費しない。2026-09-10の全体テストは97件通過した。

リモート実装の統合直後、`_find_entry`が設定された`max_arrival_delay_ms`ではなく固定60秒で停止すること、台帳が数量文字列をそのまま比較して`"2.0"`と`"2.000"`のflat決済をopen扱いすることを再現した。さらに、1分OHLCVで部分分の保有期限を許すと未来のバー内値を使うこと、Reviewerがcutoff後または別experimentの証拠IDを保存できることを確認した。いずれも修正後の全体pytestは101件通過した。

Hyperliquidのpublic candle responseは`T`をinclusive endとして返すため、`T + 1`を`close_exclusive_ms`にしなければ一分足の連続性が崩れる。collectorは受信時点で未終了の最後の足を保存せず、全体のHyperliquid pytestは104件、Binance downloader pytestは6件通過した。実ネットワーク収集はまだ行っていない。

研究設定の最小CLIは環境変数を読まず、公開JSONに`api_key`、`private_key`、wallet、secretが含まれていれば拒否する。既定`development.json`は`allow_paid_api=false`かつ`budget_usd=0`で、課金APIを送信する処理はまだ存在しない。設定テスト3件を追加して通過した。

## Review Findings：実装前に補う仕様


### R1：評価済みと送信済みを区別する（高優先）

v3の9.3〜9.4節は確定証拠と重複を重視するが、評価失敗時の消費規則が未定義。送信しただけで既評価にすると失敗日に得た貴重な取引を失う。新規証拠は、過去の正常終了レビューで評価したIDとの差分とする。正常な空patchも評価済み、不正patch/API失敗は未評価のまま。累積の7/30日集計には評価済みを含めるが、それを新規の件数へ再計上しない。patch適用の成否とレビュー評価の成否も別に保存する。

### R2：遅延patchで日次制限を二重消費しない（高優先）

v3の9.2節では保留patchを翌境界で適用し、その後新reviewを行う。両方が最大2操作・方向別0.02変更を使うと、9.4節の一日上限を超える。採用日に有効になる保留patchと新patchの合計を2操作以内、confidenceは前日の有効値からの絶対差0.02以内とする。保留適用後のversionを新reviewの親にする。上限を超える新patchは一括拒否し、切り詰めない。00:05ちょうどの完了は遅延扱い。日中はversion固定である。

### R3：仮想結果の根拠・件数・曖昧さを区別する（高優先）

v3の9.4節の20観測・50予測は、実取引と仮想結果を単純合算しない。IDを`trade:<episode_id>`と`shadow:<decision_id>`に分け、同一判断が両方に存在するときは一観測と数える。OHLCVでSL/TP順序が不明な結果、費用・funding・末尾不足の結果は校正用の二値ラベルから除外する。ルール変更には完全かつ非曖昧な根拠20判断以上、confidence補正には対象方向50判断以上を初期設定とし、実取引数/仮想結果数/対象日数をそれぞれ明示する。仮想だけの改善を実約定で裏付け済みとしない。

構造検証で「自由文ルールに本当に関連する根拠か」を完全判定できない。根拠集合をPythonが事前生成する方向・confidence帯等の集計群に限定し、LLMは群IDと観測IDを参照する。意味上の妥当性は別の監査対象として残す。

### R4：confidence補正の効果を限定する（中優先）

v3の9.4節にPythonでの補正はあるが、売買採否へ使う規則がない。初期版では校正評価専用にする。`confidence_raw`、適用offset、`confidence_adjusted`を保存し、数量は既存Risk、採否はwould_abstainを使う。補正だけの更新では売買は変わらないことをレポートへ明示する。confidence閾値による売買変更は本計画へ追加しない。元と補正後のBrier score（勝敗を0/1とした確率誤差の二乗平均）を同じ完全な証拠集合で比較する。

### R5：再開時のBatch二重送信を過剰に保証しない（中優先）

v3の13.2節は成功要求の重複送信を防ぐが、サーバー受付後に応答を失うケースを補う必要がある。送信前にローカル要求・予算予約を永続化し、job作成応答を失ったら`submission_unknown`とする。利用可能なjob一覧/表示名/要求hashで既存jobを照会し、一意に照合できない場合は自動再送しない。HTTP再試行だけで「厳密に一度」を実現したとは記載しない。

### R6：採用判定と不足時の扱いを設定にする（中優先）

v3の10節ではForward開始前の数値固定が必要。初期Developmentの暫定ゲートは50地点で形式・単位違反0、比較500地点で正常回答率99%以上、完全な採用取引30件以上とする。条件未達なら品質修正または`insufficient_data`で止め、無理に順位を付けない。候補の順位は共通地点での判断機会あたりnetとエラー率で決め、同点は少ないtoken量、候補ID順とする。層化抽出後の集計は元市場頻度で重み付けした値も併記する。

Forwardは初期案を30日・自動延長なしとし、最低取引件数、データ充足率、DD上限、費用仮定を構成固定コマンドで必須にする。暫定値は各条件100完全取引、データ充足率99%以上、DD上限25%。比較対象の単純ルールを含めたnet差を報告し、条件未達は判定不能。数値達成だけで統計的有意性や本番移行を保証しない。Development完了後に変更する場合は、Forwardデータを見る前にmanifestを改版する。

## Decision Log


2026-09-10 / Codex：v3の工程順序は維持し、上記R1〜R6を実装仕様として補う。曖昧なままコードへ移すより、証拠消費・日次変更・採算の定義を先に揃えるため。

2026-09-10 / Codex：新しい研究経路を`src/hyperliquid_ai_trader/research/`へ追加する。純粋な特徴量・Risk・判断parserは再利用し、Testnet実注文AdapterのMainnet禁止は緩めない。既存ライブDBは研究用DBへ混在させない。

2026-09-10 / Codex：依存追加は研究用Parquetの`pyarrow`を必要時に一つだけ追加する。SQLite、Decimal、標準時刻、既存公式SDKを使い、汎用イベント基盤・別取引所注文Adapter・UIは作らない。ponytailの最小構成方針に従う。

2026-09-10 / Codex：今回の成果は実装方針文書。モデル・課金設定・稼働中プロセスは変更しない。v3にある$15〜20は見積りであり支出承認額とは扱わない。

2026-09-10 / Codex：M1の初回は外部保存形式や`pyarrow`を追加せず、標準ライブラリだけで正規化・検証・特徴量計算を実装する。データ取得形式が確定する前にParquet依存を増やさず、課金・秘密鍵・ネット接続のない純粋関数として時点整合性を先に固定するため。

2026-09-10 / Codex：次の実装も標準ライブラリのDecimalとSQLiteに限定し、まずSimulatorの損益恒等式とR1の証拠消費規則を固定する。公開収集や課金APIより先に、オフラインで再現できる会計境界を検証するため。

2026-09-10 / Codex：1分足Simulatorは保有期限を分単位に限定し、到着猶予は`ExecutionConfig.max_arrival_delay_ms`を唯一の境界とする。部分分の足内価格には未来情報が混じり、設定を無視する固定猶予は実験設定の再現性を損なうため。

2026-09-10 / Codex：研究台帳のflat判定はDecimal数値比較とし、terminal reviewが消費できるのは同一experimentかつcutoff以下でclosedなepisodeだけに限定する。表記ゆれでゼロ損益証拠を失わず、未来・別実験の証拠をReviewerへ混入させないため。

2026-09-10 / Codex：初期の公開足保存はParquet依存を増やさず、正規化済みJSONLを一時ファイルから置換する。スキーマが小さく1分足の時刻監査に十分であり、取得CLIや長期収集の実測要件が固まる前の依存追加を避けるため。Mainnet readerはSDKの`Info`だけを生成し、`Exchange`・署名者・秘密鍵を受け取らない。

2026-09-10 / Codex：M3の先行実装はAPI clientではなく、公開設定を検証する`research.cli validate-config`に限定する。`allow_paid_api=false`と予算0を構造的に検証し、設定ファイルに秘密が混入しないことを先に保証してから、明示承認が必要なBatch送信を追加するため。

## Context and Orientation


作業ルートは`C:\Users\Hodaka\Downloads\div\bot_research`、対象パッケージは`hyperliquid-ai-trader`。`src/hyperliquid_ai_trader/cli.py`が現在のTestnet CLI、`runner.py`が実口座を参照するTradingServiceとLocalRunner、`trading_tools.py`がRiskと注文を結ぶ。現行`dry-run`も実市場とGeminiに接続し、オフラインSimulatorではない。Fakeによる検証をこのCLIで代用しない。

研究用の新規モジュールは必要な段階でだけ作る。`research/cli.py`は設定とコマンド、`research/data.py`は公開足・funding収集と正規化、`research/store.py`は研究台帳、`research/simulator.py`は仮想約定と口座、`research/experiment.py`は単純ルール・地点選択・Replay/Forwardの時刻処理、`research/batch.py`は要求・job・usage、`research/review.py`は日次証拠とpatch状態、`research/report.py`は比較集計とする。専用抽象クラスや大量の空ファイルを先に作らない。

共通計算は既存`features.py`にOHLCV専用関数として追加し、旧関数の出力は互換維持する。`agents.py`の関数引数検証を公開関数へ切り出し、通常/Batchの両方で使う。`gemini_gateway.py`のrequest生成・response解析を共有し、response、usage、requested/returned model ID、到着時刻を失わない結果型を追加する。`strategy.py`の旧patch経路を無言で変更せず、研究版の制限と根拠検証は`research/review.py`から呼ぶ。

## Plan of Work


### M1：確定データと証拠の基盤

最初に`research/data.py`と`research/store.py`、設定読み込みの最小CLIを作る。公開データ収集は`Info`のみで行い、署名用`Exchange`や秘密鍵を要求しない。Hyperliquid MainnetとTestnetをmanifestのvenue/networkで識別し、混在時は拒否する。既存Binance downloaderには1m intervalと開始/終了の明示指定を追加し、旧既定値は変更しない。checksumと不足分再取得の既存処理を再利用する。他研究パッケージ全体の依存を導入しない。

Binance初期期間は`[2025-09-10T00:00:00Z, 2026-09-10T00:00:00Z)`を提案既定とし、取得前に固定する。月次/日次アーカイブの公開待ちや欠損は報告する。Hyperliquidは取得時に直近足を保存し、末尾重複を再確認するcollectorを60秒間隔で回せるようにする。欠損を補間しない。UTCの正規化列は`open_time_ms`、`close_exclusive_ms`、OHLCV、`received_at_ms`、`available_at_ms`、`availability_kind`、venue、symbolとする。過去足のavailable_atは確定境界＋事前設定した配信遅延という仮定、Forwardは実受信時刻以降。HyperliquidのTは実レスポンスとの照合をfixture化し、終了境界への変換を固定する。

1分足61本の連続性・整列・finite値・OHLC整合性・非負出来高を検証し、`available_at <= decision_time`で選別する。return/vol/ATR/volumeの式はv3の4.3節をそのまま採用し、ATR分母は最新確定close。feature_setは`common_candles_v1`。未来の価格を変えても過去特徴量が変わらない検証を置く。欠損区間と直後60分は特徴量不足として記録する。

証拠は損益額で終了判定せず、EntryとExitの数量照合でflatになったepisodeだけ確定とする。取引IDはRun＋Entry decisionから固定し、部分決済で変えない。gross=0でもfeeを含む取引を残す。未帰属fillは隔離して評価不足へ反映し、slot=-1を一取引として束ねない。既存Testnet証拠を取り込むときも元DBは上書きせず、対応不明は不足として扱う。

完了条件は、秘密鍵なしで公開データ経路を作れ、61本の手計算と照合でき、ゼロ損益決済と分割決済を区別して一度だけ確定証拠へ登録できること。

### M2：Simulatorと単純ルール

`research/simulator.py`に時刻順の小さな仮想口座を実装する。金額・数量はDecimal。研究初期額1000、固定参照額250、数量小数5、既存の1%Risk・5倍・250上限・SL/TP範囲を設定ファイルへスナップショットする。取引所最小数量/価格精度は収集metadataと照合する。Risk入力の日次損益はUTC日内の確定net、日初equityはUTC境界の評価額、peak equityはRun全体で保持する。残positionがあると新規拒否する。損失上限は現行のnet日次損失として明記し、累積負け額との混同を避ける。

判断可能時刻から固定のモデル遅延＋送信遅延を足し、到達以後最初の1分境界openでEntryする。初期Developmentは合計1000ms、到達猶予60秒、max hold=約定から300秒。同一時刻は既存positionの決済、funding適用、日次締切、判断、注文の順を仕様化する。fundingは適用直前に保有していた数量に対して発生させ、境界で新規に入った数量へ遡及適用しない。正のrateならLONG支払、SHORT受取。数量と基準価格の算式を取引所ごとの保存funding metadataに残す。

OHLCV版はtrade-priceのTP/SL到達代理モデルと明記する。Hyperliquidの実trigger価格系列や清算を完全再現したとしない。同足両到達は主結果SL先・感度結果TP先を別保存する。SLを飛び越すgapは悪化したopenからmarket費用を加え、TP側gapも全量約定仮定を明記する。Exitは最初のtrigger/300秒/実験終了の早い方。足内部の正確な時刻は不明フラグを付け、境界のfunding順序で結果が変わる場合も曖昧扱いにする。含み損でDDを評価し、必要担保を割った区間は`unsupported_liquidation`として試算の信頼範囲から外す。

主費用シナリオは片道fee 0.045%、spread全幅2bps、片道slippage 1bpという仮定から始め、spread5bps・片道slippage3bpsでも比較する。過去実測値とは呼ばない。spread/slippageは価格へ一度だけ含める。fundingイベントと基準価格が不足するときは`incomplete_funding`で止めるか、費用仮定付き探索結果として別出力し、完全結果と混ぜない。数量別約定確率はOHLCVから推定せず、初期モデルは小額全量約定、部分約定/拒否は固定fixtureで検証する。

単純ルールはalways_abstain、return_5mの符号のmomentum、逆符号のmean_reversion。初期閾値0、0なら見送り、固定SL0.30%/TP0.60%。設定した他候補を探索するなら候補一覧を保存する。基準結果が赤字でも処理を成功扱いできるが、データ不足は成功成績ではない。

見送りも同じSimulatorを独立の固定参照額250で評価し、shadow結果として保存する。口座余力による拒否とモデルの見送りを分ける。主口座へshadowを加算しない。タイミング不足、TP/SL曖昧、funding不明をそのまま保持する。

### M3：Trader、Batchと予算

`prompts/research/constitution.md`、`trader_v001.md`、`reviewer_v001.md`、`configs/research/initial_strategy.json`を追加する。旧Bot用promptは上書きしない。研究Trader入力は共通特徴量、当時利用可能なcost、offsetを除いた当日strategy、SL/TP制限、max holdのみ。口座・最近の損益・板/OIを渡さない。過去return/TP幅は期待利益ではないこと、confidenceの定義と単位を明記する。raw confidenceをPythonで一度だけ補正し、記録と校正評価へ使用する。

Batchも通常APIと同じ単一open_position提案のschema・parserを使い、自動関数実行しない。見送りにもSL/TPの有限値・範囲検証を行い、Riskを通らないことで不正値がshadowへ入らないようにする。Batchから注文は出さず、保存判断を後でSimulatorが消費する。

`research/batch.py`は要求をJSONLへ固定し、要求IDをcanonical JSONのSHA-256と独立試行IDから作る。hashの対象は入力、両prompt、strategy、requested model、temperature、thinking、max output、tool schema、feature versionである。応答再利用とSimulatorの再計算キーは分け、費用/約定モデル変更時に古い損益を再利用しない。新モデルへの無言fallbackはしない。

DBで送信予約→job作成→job ID保存→結果照会→要求ごとに確定を管理する。成功回答は不変。失敗/未処理だけを追加要求にできるが、R5の応答不明は照合まで保留。通常APIもSDK内部再試行を1回に抑え、送信試行ごとに予算計上する。429を無限に再試行しない。

設定の`allow_paid_api=false`と`budget_usd=0`を既定とする。予算が指定されても上限は平均見積りでなく入力見積り＋最大課金出力tokenから予約し、実usage確認後に差額を解放する。モデル別のthinkingと出力上限の意味を実SDKで照合し、上限保証ができない場合は課金送信を拒否する。job未完了の予約を再起動で失わない。50地点の実績から500地点送信前に費用を再計算する。

最初の実API検証は課金実行の明示許可後、通常1要求とBatch1要求でschema/usage/modelを確認する。モデルメタデータ取得だけでBatch権限やquotaが確認できたとしない。既存のFree Tier用.envを自動変更しない。

### M4：点評価と3日Replay

`research/experiment.py`で最大500地点を固定する。過去return符号、volatility、volumeの層化は判断時点の値を使い、閾値はmanifestへ固定する。seed=42、非復元抽出、層不足時は余剰を他層へ割り当て、実数と市場頻度を保存する。50地点は同じ500地点の内数。candidate hashが変わらなければ追加450だけを送信する。

点評価は独立250参照額でnet/機会、net/採用取引、shadow、方向/層別、エラー率を出す。重複するepisodeから口座DDを作らない。3日Replayでは全候補のデータhashと期間を一致させ、61本の事前足とEntry/Exit余裕を除いた連続72時間を使う。Static回答はまとめて生成しても、risk/口座/注文は時刻順に評価する。判断生成済みでもposition残存、日次停止、欠損なら対応する拒否理由を残す。

max hold起点を守るため、00:05判断→00:06Entry→00:11Exitなら00:10判断の新規はposition残存で拒否される。この機会減少を仕様として記録し、次slotで早期決済して数を増やさない。比較条件すべてで同じ処理を使う。最終Entryは300秒＋到達余裕が評価内に収まるものまで。

### M5：日次Reviewerとstrategy適用

`research/review.py`でUTC日次締切・00:00待機・00:05採用を管理する。日次API呼び出しと口座管理を同じブロッキング処理にしない。最小構成は単一writerのrunnerとReviewer用Future一つ。workerはモデル応答だけを返し、DB書込みはrunnerが行う。期限超過応答は保存して翌境界の候補にする。再起動はDBの状態から再開し、当日versionを変更しない。

日次スナップショットは`available_at <= cutoff`の全確定episodeから作る。当日/7日/30日の件数、gross、fee、funding、net、方向、confidence、strategy別をSQL/Pythonで集計し、履歴が短ければ対象日数を出す。代表例は方向×勝敗×confidence帯の時刻順先頭から上限12件とする。累積集計、未評価ID集合、shadow集計を別フィールドにする。使用可能な根拠群とID一覧を保存し、LLM指定のIDをその集合と照合する。

review状態はprepared、in_flight、failed、validated_no_change、validated_patch。patch状態はpending、applied、rejected、stale。API失敗/不正patchでは未評価IDを消費せず、次日へ再提示する。正常空patchでは消費するがversionを増やさない。非空patchは文書の2操作、文字数、16KiB、offset±0.2、日次差0.02、根拠20/50制限を一括検証する。複数patchの日次合計はR2に従う。strategyと適用済みpatch IDを一トランザクションで更新し、JSONファイルはDBから生成する写しとする。

Adaptiveの3日ReplayはDay Nの確定→review→翌日version固定→Day N+1回答生成の順に実行する。実API待ち時間と市場内の仮想遅延は分ける。Day3後のreviewは動作確認であり3日損益の改善には寄与しない。成功判定は更新回数や利益でなく、空patch/失敗/遅延/再起動で規則が維持されること。

### M6：Forwardの構成固定と同時比較

`research/cli.py freeze`がprompt/strategy/feature/cost/遅延/モデル/生成設定/コード/データ条件のhash、30日期間、R6の判定条件、予算をmanifestへ保存する。未指定の予算や採用条件では開始できない。稼働中の設定差異は当該実験を区切り、新Runとする。

単一collectorから同時刻の観測をStatic、Adaptive、単純ルールへ渡し、口座は分ける。両LLM要求を同じ観測で開始し、到着時刻を記録する。Forwardは通常API。00:00待機は全条件共通。到着遅延で期限外ならmissedとして保存し、古い注文をまとめて出さない。注文・Exit管理はLLM待ち中も動かす。

marketの利用可能な1分足・spread・fundingを継続保存する。OHLCV版と同じ約定規則を主比較に使い、実測板による価格評価は別versionの感度結果として記録する。収集中断は欠損扱いで、その間の仮想実約定を後から確実な約定として埋めない。

`research/report.py`は取引費用後net、モデル等の運転費用後、研究開発費を分ける。Static/Adaptiveの同期間・日別差を主比較とし、欠損、正常見送り、Risk拒否、API失敗、曖昧episodeを列挙する。30日終了時は期限内にcleanupし、終端未確定があればpartialを明記する。利益が良い日の早期終了は行わない。

### M7：Testnet監査

共通入力を使う研究Traderと検証済み判断を、明示的なTestnetコマンドから既存TradingToolsへ渡す。現在の`create_hyperliquid_adapter`の固定URLは保持する。研究のMainnet Info readerは署名用オブジェクトを持たない。Testnet口座状態・秘密鍵をForwardの仮想口座へ渡さない。

判断開始、応答到着、発注、約定、TP/SL受付確認、決済/取消完了の時刻を保存し、Entry起点の保有期限を検証する。終了時に建玉とBot注文ゼロを確認する。Testnet損益をMainnet研究の採用判断へ混ぜない。

## Interfaces and Dependencies


研究用設定は`configs/research/development.json`と`forward.json`。秘密を含まないJSONとし、日付、venue、手数料/遅延、生成設定、候補上限、日次制限、採用条件を格納する。APIキーだけ既存環境から必要時に読む。

`NormalizedCandle`はopen/close_exclusive/received/availableの各ms、OHLCV、venue、availability_kindを保持する。`features.build_common_candle_features(candles, *, decision_time_ms)`は決定時刻までの確定61本を検証し、共通特徴量dictを返す。`agents.parse_trade_calls(calls)`は同一schemaの提案をTradeDecisionへ変換し、研究側の制限検証を続ける。

`research/simulator.py`には`simulate_episode(decision, candles, funding, execution_config)`と`advance_account(account, events, until_ms)`を置く。前者は実取引予定とshadowの共通の価格・費用関数、後者は保有量・口座・UTC日次状態を更新する。Replayに実sleepは使わない。

研究DBは`data/research/research.db`。最小テーブルはexperiments、decisions、episodes、account_events、reviews、review_evidence、strategy_versions、requests、batch_jobs。複雑な分析列はsnapshot JSONで保持し、検索・一意性に必要なID・時刻・statusだけ列にする。decisionsの一意キーはexperiment/arm/decision_time、episodesはdecision/kind、reviewsはexperiment/arm/UTC日、strategyはexperiment/arm/version、review_evidenceはreview/evidenceで固定する。

episodeにはEntry/Exit数量・時刻・価格、closed status、gross、fee、funding、net、quality、strategy versionを持つ。review_evidenceは送信と評価完了を分け、同じIDを累積文脈として再利用できる。requestsはcache key、独立試行、状態、予約費用、usage、実費推定、raw response参照を保持する。

Parquetは`data/research/`へ保存し、raw応答とDBもGit除外。設定・prompt・本計画・匿名化集計はGit管理候補。大きな入力やAPI応答をresultsへ誤って入れない。既存`.gitignore`のdata/reports除外を再利用し、追加ディレクトリを作る場合だけ明示的に調整する。

## Concrete Steps


以下は実装後に提供するCLI契約。現在は存在しない。作業場所は`C:\Users\Hodaka\Downloads\div\bot_research\hyperliquid-ai-trader`。

    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli validate-config --config configs/research/development.json
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli collect --config configs/research/development.json
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli verify-data --config configs/research/development.json
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli baseline --config configs/research/development.json
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli points --config configs/research/development.json --count 50 --prepare-only
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli batch --config configs/research/development.json --action submit
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli batch --config configs/research/development.json --action sync
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli points --config configs/research/development.json --count 500 --prepare-only
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli replay --config configs/research/development.json --days 3 --reviewer off
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli replay --config configs/research/development.json --days 3 --reviewer on
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli freeze --config configs/research/forward.json
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli forward --config configs/research/forward.json
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli report --config configs/research/forward.json

submit/通常API/Forwardは予算と明示の実行許可がある段階で行う。prepare-onlyとFake fixture検証はネット接続・課金不要。collectは公開市場データだけを取得する。各コマンドはexperiment ID、件数、quality/status、出力パスを返す。例えば不足時は`status=insufficient_data`と欠損区間を返し、利益0で成功としない。

## Validation and Acceptance


テストは既存pytestを利用し、研究用の重要な境界に絞る。データは未来足拒否、61本、欠損、単位。SimulatorはLONG/SHORT、SL/TP同足、gap、Entry遅延、300秒、日跨ぎfunding、gross=0決済、分割決済、損益恒等式を確認する。集計は100件超・API失敗後の未評価維持・同一IDの二重計上なしを検証する。

BatchはFakeクライアントで50→500が450追加、応答喪失で自動再送なし、予算予約の再起動、入力hash不一致でcache不使用を確認する。Reviewerは架空/未来/重複ID、20/50件未達、空patch、不正patch、00:05遅延、保留と新patchの合計上限、日中version固定、confidence一回補正を確認する。口座とLLMを切り離し、保存回答のBatch/逐次で同じ台帳になる短い統合fixtureを置く。

一分足open=100でLONG数量1、close=101、fee率0.00045なら、spread/slippage/fundingを0に明示したfixtureでgross=1、fee=0.09045、net=0.90955となる。これは算術fixtureであって費用ゼロの主実験ではない。常時見送りのfixtureではshadowが勝っても口座equityは変わらない。日次00:04:59のpatchは採用、00:05:00は翌日候補となる。

実装完了時は既存テスト＋追加境界テスト、`git diff --check`、秘密情報混入確認を行う。現行Testnetの安全境界が維持されることを検証する。文書作成のみの今回、pytestや実験は実行せず、参照パス・数式・差分を検証する。

## Idempotence and Recovery


実験設定を変更したら新experiment IDとし、過去結果を上書きしない。足はvenue/symbol/open時刻で一意化し、取得済みhashが変わった場合は訂正版として別保存する。成功Batch要求を再送せず、応答不明は照会で解決するまで保留する。未解決予約費用を勝手に解放しない。

SQLite更新は一トランザクションで、decision消費とaccount更新、patch適用とversion更新を原子的に行う。起動時に同一experimentの二重writerを防ぎ、未処理位置から再開する。停止中のForward slotはmissed。後追い予測を当時の予測として追加しない。途中障害で欠けた結果はpartial/insufficient_dataを保存する。

既存`data/trader.db`を変更する実装段階では事前にSQLite backup APIで一貫したバックアップを取得する。研究用DBは別ファイルなので、初期工程ではライブDBの移行は不要。Testnetで未知のpositionがあれば既存preflight方針に従い停止する。

## Artifacts and Notes


公式照合日2026-09-10。[Gemini料金表](https://ai.google.dev/gemini-api/docs/pricing)は2.5 Flash-Lite通常input/output $0.10/$0.40、Batch $0.05/$0.20、3.6 Flash通常$0.75/$3.75（100万tokenあたり、2026年末まで）で、v3の前提と一致した。仮定の入力/出力2000/500と8000/2000を用いると開発$1.4045、30日Static/Adaptive $7.3170、合計$8.7215となる。最大出力による予算予約は平均費用試算より大きくなる。

[2.5 Flash-Lite仕様](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite)でBatchとFunction Calling対応を確認。[Batch仕様](https://ai.google.dev/gemini-api/docs/batch-api)を利用するが、個別プロジェクトの課金権限・quotaと実SDK互換は未検証。[Hyperliquid Info](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot)の直近5000足制限と、[Binance公開データ](https://github.com/binance/binance-public-data)のUSD-M系列を確認した。PR #4はWeb経由で取得できなかったため、PRの内容やレビュー状態は本判定の根拠としていない。

## Outcomes & Retrospective


v3は研究設計として採用可能。無条件に「問題なし」ではなく、証拠消費、日次patch合算、仮想根拠、confidenceの効果、Batch応答喪失、採用基準を補完した。本計画がこの補完を含む実装仕様となる。M1/M2の中核として、確定時刻を持つ1分OHLCV、未来情報を拒否する共通特徴量、研究専用証拠台帳、費用込みSimulator、単純ルール、shadowと口座の分離を実装した。リモート実装統合後に、設定どおりの到着猶予、分単位の保有期限、Decimal数量のflat判定、Reviewerのcutoff/experiment境界を補正し、101件のpytestで検証した。M1ではさらに、署名なしのHyperliquid公開足reader、未確定足除外、JSONL保存、Binanceの1分足入力を追加し、Hyperliquid 104件・Binance 6件を通した。M3では課金なしの公開設定検証CLIと支出拒否を追加した。公開データの実取得、funding event、Batch、Replay、Reviewer、Forwardは未実装であり、実取引・課金API・市場データ収集はこの時点でも実行していない。

変更履歴：2026-09-10、housinv3.mdのレビューと現行コード照合に基づいて初版作成。元文書を保持し、計画の重複作成を避けるためレビュー指摘と具体的工程を本ファイルへまとめた。

変更履歴：2026-09-10、M1の証拠台帳とM2のオフラインSimulatorの実装結果、検証件数、残作業を反映した。計画と実装状態を一致させるため。

変更履歴：2026-09-10、リモートmainのM1/M2実装を取り込み、時点整合性と証拠隔離を保つ修正・101件の検証結果を反映した。

変更履歴：2026-09-10、M1の署名不要なHyperliquid公開足reader、JSONL保存、Binance1分足入力、検証結果を反映した。

変更履歴：2026-09-10、M3の公開設定検証CLI、課金拒否の既定、テスト結果を反映した。
