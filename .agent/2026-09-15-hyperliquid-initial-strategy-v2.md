# Hyperliquid Initial Strategy v2：市場状態・費用・保有時間を分離する実装計画

このExecPlanはルートの`PLANS.md`に従う。実装者はProgress、Surprises & Discoveries、Decision Log、Outcomes & Retrospectiveを更新する。2026-09-15時点の成果物は実装計画であり、分析結果や採用済みstrategyではない。

## Purpose / Big Picture


5分ごとの判断周期を維持し、15〜60分の市場状態から取引に参加する価値を調べる。Pythonだけで各判断時点のLONG/SHORTと5・10・15・30分保有を比較し、費用控除後の成績が安定する条件だけを初期strategyへ採用する。採用条件がなければ、ルールを空のまま研究を終了できることも正しい成果とする。

本計画は`.agent/2026-09-10-hyperliquid-research-v3.md`のStage 1/2を具体化する差分計画である。既存研究基盤、履歴、ライブBotを作り直さない。Stage 1A Holding horizon analysis → Stage 1B Multi-horizon regime analysis → Stage 1C Cost-aware candidate comparison → Stage 2 Initial Strategy v2 → Freeze → Stage 3 Trader prompt comparisonの順に進める。Stage 3以降の実験自体は本計画の対象外とする。

## Progress


- [x] (2026-09-15) 現行strategy、Simulator、費用、特徴量、baseline、入力準備、freeze、既存計画を確認した。
- [x] (2026-09-15) ユーザー方針を変更箇所、評価契約、分析順序、採用条件、検証に落とし込んだ。
- [x] (2026-09-15) M1：v001保存とStage 1実験仕様・入力検証。
- [x] (2026-09-15) M2：全判断機会の独立方向・保有時間ラベル生成。
- [x] (2026-09-15) M3：学習期間だけで決める分位境界と候補A〜Eの比較。
- [x] (2026-09-15) M4：非重複口座Replay、頑健性評価、採否レポート。
- [x] (2026-09-15) M5：v001維持、freeze、研究文書の更新。
- [x] (2026-09-15) 条件付き追加：common_candles_v1で候補不成立のためfeature v2は開始しない。
- [x] (2026-09-17) 追加診断：採用判定を検証期間の方向別件数・月別正率と口座Replayの安全条件へ分離した。保存済み入力から監査用の独立成果物を再作成し、88構成の全fold損失、採用候補ゼロ、費用分解を確認した。

## Context and Orientation


調査基準commitは`3a54b0f3bedca84cf960578aa7d1e139215a690c`。以下のパスは特記しない限り`hyperliquid-ai-trader/`からの相対パス。作業コマンドも同ディレクトリで実行する。

`configs/research/initial_strategy.json`はschema_version=2、version=1、feature_set=common_candles_v1、active_rules=[]である。5分momentum採用済みではない。`initial_strategy_provenance.md`は2025-09-01〜2026-08-31 UTCの365日を対象とした過去分析の記録であり、連続口座がDD上限で停止したことも記載する。本計画作成ではその分析を再実行・再検証していない。

`src/hyperliquid_ai_trader/research/data.py`の`CandleSeries.build_features(decision_time_ms=...)`は利用可能な確定61本から8特徴量を生成する。判断周期は`RESEARCH_DECISION_INTERVAL_MS=300000`。`simulator.py`の`ExecutionConfig.max_hold_ms`は正の整数分を受け付ける。現在の300000は既定値であり5分以外を拒否する制限ではない。

`baseline.py:run_baseline`は3旧ルール用の非重複Replay、`risk.py:ResearchRiskEngine`は数量・損失制限、`binance.py:FundingSeries`はFunding支払と欠損検出を担当する。`evaluation.py`は検証済みLLM判断の地点評価なので、新しいPython研究をLLM要求・応答形式へ無理に変換しない。`costs.py`の費用関数とSimulatorを共用する。

`points.py`は最大500点のLLM比較用抽出であり、全期間のStage 1には使わない。`preparation.py`はfeature/strategy/executionをTrader入力にし、`freeze.py`は設定・コード・artifact・promptのhashを固定する。既存データは`data/research/binance-BTCUSDT-1m.jsonl`、過去結果は`data/research/binance-baseline-analysis-2025-09-01_2026-08-31.json`に存在する。存在は品質・期間充足の証拠ではないためM1で検証する。

## Surprises & Discoveries


`data.py:_realized_volatility`は累積変動幅ではなく1分リターンの母標準偏差を計算する。5m/30mは標準偏差の推定窓なので両者を比較するときにsqrt(30/5)補正はしない。ATRは百分率、returnとvolは比率である。

`costs.py:estimated_round_trip_cost_bps`は既に存在する。現在の設定では2×0.00045×10000 + 2 + 2×1 = 13 bps。Fundingは含まない。これは設定上の近似往復費用であり、現行取引所料金を新たに調査した値ではない。

2026-09-17の監査では、既存の`_promoted_candidates`が全期間の口座Replay件数と方向別損益を使い、設定済みの検証期間の方向別最低件数と月別正率を確認していなかった。候補が不採用だったため過去の結論は変わらないが、将来の誤採用を防ぐため修正が必要である。

既存のDD停止判定は、`decision_status_counts`のstatus名に`max_drawdown`を探していた。しかしReplayでは上限到達後を`risk_rejected`、理由を`max_drawdown`として保存するため、停止は検出されなかった。理由別件数を別に集計する必要があった。

保存済みepisodeは約定後のentry/exit priceを保持していないが、quantity、gross PnL、fee rate、feeから2価格の和を復元できる。gross PnLから差を復元して、共有`costs.execution_price`と同じ線形cost modelを逆算すると、約定調整前価格・spread・slippageを再結合できる。有限Decimalの逆算累積には最大1e-18 USDの丸め残差があるため、それより大きい不一致だけをエラーにする。

Simulatorの`gross_pnl`にはspread/slippageの不利約定が既に反映され、netからさらに引いてはいけない。また`simulate_episode_from_entry`は`candles[entry_index:]`を作るため、約10万判断×8結果では大きなリストコピーが繰り返される。M2で期限までのインデックス走査へ局所変更する。

`run_baseline`はepisodeの将来決済を計算して直ちに口座へ反映している。長期hold用の新Replayは決済時刻まで結果をpendingに置き、途中slotの口座状態に未来の損益を反映させない。旧baselineの結果を無断で書き換えず、共有化する場合は同じ回帰検証を行う。

Binance Funding CSVには一部、予定8時間時刻から1msずれた`calc_time`があった。FundingSeriesと同じslot正規化で入力検証することで、実在するFundingを不足扱いせず、未存在のscheduled slotは引き続き拒否する。

実データでは先頭の61本特徴量要件と末尾のentry/exit余裕により256ラベルが`incomplete_price`となった。内部価格gapやFunding gapは検出されず、境界不足を0 PnLとして扱わなかった。

## Decision Log


2026-09-15：最初はcommon_candles_v1を維持する。既存指標で中心仮説を測れるため、MACD/RSIやfeature v2を先行追加しない。return_1m/5mは既存形式に残すが新候補の方向条件には使わない。

2026-09-15：独立ラベルと運用Replayを分ける。前者は市場状態の条件付き期待値、後者は資金・ポジション占有を含む実行可能性を測る。独立ラベルの合計を口座の年間利益やDDとして報告しない。

2026-09-15：SL/TPも結果を左右するため、holdのみの診断と共通SL/TP付きの比較を併記する。まず現行SL=0.30%、TP=0.60%を比較用の対照条件として使用し、採用値とは扱わない。探索条件・検証条件を結果を見る前に記録する。

2026-09-15：全1年で最適閾値を決めて同じ1年を検証扱いしない。旧5分研究で既に使った年なので、後半を分割しても完全な未見データという主張はしない。将来のHyperliquid比較は別途必要である。

2026-09-15：実データで探索選出されたD/Q75/固定SL-TPの4保有時間候補は、すべてRiskの25%最大DDへ到達した。境界不足による品質`partial`とは別に、Binance根拠としてv002を作らず、v001と空のactive rulesを維持する。

2026-09-17：採用可否では、固定notionalの独立ラベルから得る検証期間の件数・各方向の正損益・月別正率を先に判定し、非重複の年間口座Replayは最大DD停止を確認する別の安全条件として扱う。価格変動と執行費用の比較は保存済みepisodeから復元し、候補ごとの別口座Replayを合算しない。

2026-09-17：既存のfreeze済み成果物は書き換えない。追加した`conditional-diagnose`は、同じ固定入力を再集計した`data/research/conditional-edge-v2/diagnostic-run/`だけを読み書きする。local runtime dataはGit除外なので、コード・テスト・文書と研究結果の実データを混在させない。

## Plan of Work


### M1：履歴保存と再現できる研究設定


`configs/research/initial_strategy_v001.json`へ現行strategyをbyte同一で保存する。既存`initial_strategy.json`、provenance、`state/strategy.json`は保持する。v002は採用判定まで作らない。

新規`configs/research/conditional_edge_v2.json`は既存`binance_development.json`への参照と研究固有設定を持つ。既存ResearchConfigへ探索項目を混ぜず、新規`research/conditional_edge.py`で読み込む。設定はschema_version、experiment_id、base_config、UTCのstart/end（終端除外）、decision_interval_ms=300000、holds_ms=[300000,600000,900000,1800000]、feature_set=common_candles_v1、exit_profiles、folds、quantiles、candidate_specs、promotion_policyを持つ。相対参照は設定ファイルの位置基準とする。金額はDecimalで処理・文字列保存する。

初期対象は履歴と同じ2025-09-01T00:00:00Z〜2026-09-01T00:00:00Z。最初の6か月を探索期間とし、以後の月を順に、その月より前のデータだけで境界を推定する月次検証にする。最後の2か月は候補・exit条件を固定した確認期間とする。全期間表は記述統計と表示する。前側61本と後側の最大Entry遅延+30分+決済openのデータを要求範囲と区別して記録する。分割境界を跨ぐ学習ラベルは除外し、未来の評価月から結果を取り込まない。

既存importのmanifestとsource SHA-256を検証し、Funding CSVも期間・hashを固定する。欠損・市場不一致・破損を握り潰さない。期間の冒頭・末尾不足は判断slotごとに記録し、内部gapは既存の連続性検証で入力エラーとする。新規ダウンロードや期間の勝手な置換は行わない。

M1の完了条件はv001のhash一致、設定読込、日付・hold・費用・分割の不正値拒否、実データの要求/実測期間と欠損の一覧が出ること。結果計算前に設定hashを保存する。

### M2：Stage 1A 独立ラベルとhold比較


`research/conditional_edge.py`に全5分slotを列挙する経路と独立ラベル生成を追加し、`research/cli.py`に`conditional-labels`を追加する。特徴量は一時点につき一度だけ構築する。LONG/SHORT×4 holdを同じ入力・費用・固定参照notional=250で計算する。数量は約定entry priceに対する固定notionalとして定義し、Risk連動数量と混同しない。

exit_profileは`hold_only`と`fixed_sl_tp`の2種類。前者もfee/spread/slippage/Fundingを適用し、SL/TPを使わず期限で決済する診断専用。後者は既存の0.30/0.60%を共通対照とする。`simulator.py:ExecutionConfig`へ後方互換の`exit_policy`を追加し既定を現行動作にする。hold_onlyを極端なSL/TP値で擬似実装しない。期限はdecisionからでなく実際のentry起点。同足SL/TPはstop_firstを主結果、take_firstを感度分析とし、曖昧件数を表示する。

`features.jsonl`と`labels.jsonl`を分離する。前者はdecision_time_ms、feature_set、as_of_ms、既存features、時点で既知のcost派生量だけを含む。後者はdecision_time_ms、side、hold_ms、exit_profile、quality/status、entry/exit時刻、exit_reason、quantity、gross_pnl、fee、funding、net_pnl、net_return_bpsを持つ。欠損の損益はnull、理由は価格不足/Funding不足に分ける。future labelをTraderのpoint形式として読めないartifact_typeにする。

有効なN時点なら各exit_profileでN×8行が存在し、不足も行として残す。同じ時点でhold間の利用可能範囲が違う場合、主比較は全hold・両方向で完結する共通集合を使い、個別の完結集合は補足表にする。共通集合の外側を消去せずcoverageへ記録する。Fundingの将来実測値は結果の費用にだけ使い、entry時点のcost featureには使わない。

Simulator走査はentryから期限の足までとし、年データの残り全体を毎回コピーしない。JSONLは順次保存し、全episodeオブジェクトを一括保持しない。新しい並列実行基盤やデータベースは追加しない。1日/1か月で所要時間・出力容量を測ってから1年を実行する。

### M3：Stage 1B/1C Regime分類と候補比較


市場状態の分類をregime、学習期間内の値を等頻度に区切る境界を分位と呼ぶ。`conditional_edge.py`に純粋関数`derive_regime_features(features, execution)`、`fit_regime_thresholds(training_rows, quantiles)`、`candidate_decision(candidate_spec, features, thresholds, exit_profile)`を追加する。方向・見送りは未来ラベルを引数に取らない。結果集計は新規`research/conditional_report.py`が担当する。

単位を揃え、cost_bps=既存費用関数、atr_bps=atr_pct×100、vol_30m_bps=realized_vol_30m×10000とする。atr_to_cost=atr_bps/cost_bps、vol_to_cost=vol_30m_bps/cost_bps、vol_expansion_ratio=realized_vol_5m/realized_vol_30m。vol_to_costは1分変動の尺度であって将来収益予測ではない。分母ゼロはnullと理由を記録し、無限大に置換しない。zero costは費用感応テストだけに許し比率条件は評価不能とする。実験の本番設定は正の往復費用を必須にする。

alignmentはup（15m/60mとも正）、down（とも負）、mixed（逆符号）、flat（どちらか0）の4区分。ATR、vol30、volume_zscore、expansionを探索期間の四分位で単独集計し、alignmentとの二次元表までを主分析にする。少数セルを含む全組合せ探索はしない。同値の分位境界は統合し実際の境界を保存する。

候補Aはsign(return_15m)、Bはalignmentのup/downのみ。CはBにatr_to_costとvol_to_costの下限をANDで追加し、DはCにvolume_zscore下限を追加する。EはBにexpansion比の下限を追加する独立候補とする。C/Dの費用gateを通ってから方向を評価する。A/Bはgate有効性を見る比較対照。0、不整合、必要な派生値がnullなら見送り、missing featureはデータ不足として区別する。Mean Reversionは旧対照の記録だけに残し、新候補に含めない。

初回は分位別表を作り、C/D/Eの下限候補を探索期間のQ25/Q50/Q75から少数だけ選ぶ。直積で最良値を大量探索しない。候補A〜Eの診断比較後、独立検証へ進める候補を2〜4個に絞り、選んだ理由と試した全構成を保存する。数値閾値、候補名、exit_profile、hold、costをcandidate_idのhashへ含める。検証月の境界推定に当該月の値を使わない。

SL/TPについても対照条件を最終採用値にしない。探索期間のhold_only結果の値幅分布を参考に、既存decision制限内で最大2組の追加SL/TPを設定へ記録し、候補と同じ分割で比較する。追加理由がなければ対照1組で進め、その条件しか検証していないと報告する。最終確認期間を見てから値を再調整しない。

### M4：非重複運用Replayと採否


新規`research/conditional_replay.py`に`run_candidate_replay`を実装する。Simulator、FundingSeries、ResearchRiskEngine、VirtualAccountを利用し、slotごとに「時刻までに確定したpending決済の反映→UTC日付更新→特徴量と候補判断→占有/Risk確認→新規episode予約」を行う。決済前の未来PnLを口座・数量・日次制限へ見せない。保有中は再entry・反転・期限延長をしない。決済時刻と同じslotは決済反映後に新規判断可能とする。

独立ラベルは重複可、口座は1ポジションのみ。各slotはexecuted、signal_abstain、position_blocked、risk_rejected、incompleteの排他的区分を持つ。優先順位は入力不足→position_blocked→signal_abstain→risk_rejected→executedとし、signal側の見送り判定を別フィールドに保存できる。Funding不明の取引以後は口座状態も未確定になるため、後続の口座実行成績を確定扱いしない。独立ラベル分析は継続できるが、口座Replayはその時点でpartialにする。

`conditional_report.py`は固定notionalの独立成績とRisk適用口座成績を別セクションに出す。net PnL、net expectancy/trade、net expectancy/decision opportunity、trade count、abstain rate、fee total、profit factor、最大DD、median trade、LONG/SHORT別、UTC月別、volatility regime別を出す。gross、Funding合計、spread/slippage設定、曖昧率、占有率、Risk拒否率、coverageも添える。

decision opportunityは要求された評価期間の5分slot数を母数とする。完全データの場合はnet PnL/全slot数で、見送り・占有・Risk拒否も母数から落とさない。欠損slotがあれば全機会期待値はnull（partial）とし、観測完結集合の参考値を別名で表示する。共通完結集合の比較表にも元の要求母数と除外件数を併記する。取引0のexpectancy/trade・median・profit factorはnull。損失0のprofit factorもnullと理由を返しInfinityをJSONに出さない。損益0の取引を除外しない。

DDは口座の決済済みequityのpeakからの下落として明記する。含み損込みDDや清算リスクを評価したと主張しない。月別の年次口座成績は連続Replayを月へ帰属し、月ごとに資金を戻して合算しない。独立月リセットを補足する場合は明示する。regimeはentry判断時点、取引月はentry UTC月、口座損益計上はexit UTC時刻と定義し、跨月を説明する。

採用判定は`promotion_policy`を実行前に保存して機械的に理由を出す。初期の研究用基準案は検証期間200取引以上、各方向50以上、検証月の過半数でnet expectancy/decisionが正、検証全体の両方向net expectancyが正、最大DDが既存25%上限未満、上限到達による停止なしとする。これは統計的十分性の保証でも最適数値でもなく、結果を見て緩めないための事前基準である。

閾値の分位位置±0.05とhold隣接候補（端点は片側）で同じ集計を行い、費用込み期待値の符号が崩れる候補を採用しない。各感度は一つずつ変え、組合せ最適化しない。片方向しか支持されなければ両方向strategyとして採用せず、片方向限定は別仮説として報告する。費用1.25倍とtake_firstとの差も表示する。重複ラベルを独立標本として有意差を算出せず、必要な区間推定は日単位のブロック再標本化、固定seedで行い不確実性を示す。

品質status（ok/partial/insufficient_data）と研究結論（supported/no_supported_candidate/inconclusive）を分ける。ファイル生成成功や損失0をedge発見としない。最後の確認期間で不合格なら採用せず、探索へ戻る場合は新experiment_idと別の未使用確認期間を必要とする。

### M5：初期strategy・freeze・文書への反映


採用基準を満たした場合だけ`configs/research/initial_strategy_v002.json`を作る。schema_version=2は既存形式の番号であり、strategyのversion=2と混同しない。parent_version=1、feature_set=common_candles_v1を基本とする。active_rulesには採用した候補の数値・単位・AND条件・見送り条件を明記し、採用しなかった出来高/expansionルールを一般論として追加しない。confidence_calibrationはlong/shortとも0、last_review_cycle=0とする。provenanceは別ファイル`initial_strategy_v002_provenance.md`に期間、入力hash、候補、全試行、検証結果、落選理由、費用仮定、Binance由来の制約を残す。

採用不可ならv001とactive_rules=[]を維持し、reportにno_supported_candidateまたはinconclusiveを残す。空ルールだけで実際のLLM出力が必ずABSTAINになるわけではないため、Stage 3移行可否はstrategy内容ではなく採用report/freezeの条件で判定する。

`freeze.py:build_freeze_manifest`の既存artifact_paths/rulesへ、採用report、version付きstrategy、研究設定、分位境界、候補条件、feature定義、hold、cost、SL/TP、risk、期間分割を含める。`preparation.py`と関連CLIではv2実験を要求した際、採用判定とfreeze整合を検証する。既存v1試験まで破壊する一律gateにしない。保有時間は固定execution入力で、LLMが変更できる出力フィールドを追加しない。

`README.md`と`housinv3.md`のStage 1/2に新しい工程とコマンドを反映し、旧5分研究を履歴と明記する。既存v3 ExecPlanへ本計画の参照を追記し、過去の完了記録を削除しない。研究Trader promptの役割説明は「tradeability→regime→LONG/SHORT/ABSTAIN」に揃えるが、prompt比較の実行はここでは行わない。

### 条件付き追加：common_candles_v2


v1のregime表でtrend/chopの分離不足や短期returnへのアンカーが観測された場合に限り別experiment_idで追加する。v1クラスのキーを破壊的に削除しない。新feature契約、config検証、pointsの読書き、preparation、request hash、freeze、テストをまとめて対応する。

v2候補は15m/60m return、vol5/30、expansion比、ATR、volume、trend_efficiency_15m、range_position_60m、costとmovement比。efficiency=abs(C_t-C_t-15)/sum(abs(C_i-C_i-1))（15差分）、range_position=(C_t-min low60)/(max high60-min low60)。分母0はnullで品質を明示する。movement_to_cost_ratioはatr_bps/cost_bpsと明示し、期待収益を意味させない。return_1m/5mはv2 Trader入力から除外する。既存costはexecutionにも存在するので二重定義せず同じ関数から作る。追加有用性は同じ候補・hold・分割でv1との比較を行い、支持がなければv1を維持する。

## Interfaces and Dependencies


新規モジュールは`src/hyperliquid_ai_trader/research/conditional_edge.py`（研究設定・特徴派生・ラベル・候補）、`conditional_replay.py`（確定時刻順の口座）、`conditional_report.py`（集計・採否・出力）の3つを基本にする。既存のdataclass、Decimal、statistics、json、hashlib、pathlibを使い、外部計算サービスや新規指標ライブラリは導入しない。

`conditional_edge.py`で`ConditionalStudyConfig`（M1の設定）、`CandidateSpec`（candidate_id/name、閾値、exit_profile）、`ConditionalLabel`（M2の列）を定義する。`iter_conditional_labels(*, candles, funding, research_config, study_config)`はConditionalLabelのiterator、`candidate_decision(*, candidate_spec, features, thresholds, exit_profile)`は既存TradeDecisionを返す。`run_candidate_replay(*, candles, funding, research_config, study_config, candidate_spec)`はslot別区分とepisode、coverage、品質statusを持つ結果を返す。データ不足の区分をwould_abstainへ押し込めない。

manifestにはartifact_type/schema_version、設定hash、入力hash、コードcommit、market、要求/観測期間、fold、hold、exit profile、cost model version、seed、行数、qualityを必須とする。下流CLIは親hashを検証する。研究labelのartifact_typeをpointsとして渡した場合は入力を拒否する。

## Concrete Steps


実装順はM1→M2→M3→M4→M5。以下は追加するCLIの契約であり、計画作成時点では未実装である。PowerShellの作業場所を固定する。

    Set-Location C:\Users\Hodaka\Downloads\div\bot_research\hyperliquid-ai-trader
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli conditional-labels --study-config configs\research\conditional_edge_v2.json --candles data\research\binance-BTCUSDT-1m.jsonl --funding-csv $fundingPath --output-dir data\research\conditional-edge-v2
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli conditional-analyze --study-config configs\research\conditional_edge_v2.json --input-dir data\research\conditional-edge-v2 --output-dir data\research\conditional-edge-v2\analysis
    .\.venv\Scripts\python.exe -m hyperliquid_ai_trader.research.cli conditional-replay --study-config configs\research\conditional_edge_v2.json --candles data\research\binance-BTCUSDT-1m.jsonl --funding-csv $fundingPath --analysis-dir data\research\conditional-edge-v2\analysis --output-dir data\research\conditional-edge-v2\replay

`$fundingPath`はM1で期間・hashを検証した既存Funding CSVの絶対パスを設定する。ファイル名を推測せず`../binance-btcusdt-futures-research/data/research/`から対象期間とmanifestで特定する。存在しない場合は不足を報告する。CLIは進捗・coverage・品質status・採否を別に出し、partialは非成功終了コードと成果物を残す。最初に同じCLIを1日の検証用設定で実行し、その後1か月、365日の順に増やす。

生成物はfeatures.jsonl、labels.jsonl、各manifest、analysis/regime_summary.json、analysis/candidates.json、replay/episodes.jsonl、replay/decisions.jsonl、replay/report.json、replay/report.md。探索で候補仕様を変更したら新しい設定hash/出力先とし、固定済み結果へ追記しない。

## Validation and Acceptance


追加テストは`tests/test_research_conditional_edge.py`、`test_research_conditional_replay.py`、`test_research_conditional_report.py`に分ける。未来の足変更でtのfeatureと候補判断が不変、同じtのlabelは変化可能、未到着足不使用、分割後の値で学習分位が変わらないことを確認する。return_5mだけ符号反転してもA〜Eの方向が不変であることを確認する。

費用13bps、ATR=0.10%→10bps、ratio=10/13、vol30=0のexpansion=nullを検証する。flat相場のhold_onlyは費用分だけ負、Funding欠損はnull、同足SL/TP、Entry遅延、全4holdの期限決済を確認する。N時点に各profileの8N行があり、末尾不足を0PnLとして数えないことを確認する。

10:01 entry/10:16 exitの15分hold例では10:05/10:10/10:15はposition_blockedで、決済PnLは10:16前の口座に反映しない。UTC跨日・跨月、Funding不足後の口座未確定、DD制限、見送り100件中5取引なら分母100、ゼロ取引のnull指標、正負ゼロの各取引、同じhashで再実行同一結果を検証する。各slot区分の合計が要求slot数と一致しなければ失敗とする。

テスト実行は次を使う（新規テストは実装後に存在する）。

    .\.venv\Scripts\python.exe -m pytest -q tests/test_research_conditional_edge.py tests/test_research_conditional_replay.py tests/test_research_conditional_report.py tests/test_research_simulator.py tests/test_research_baseline.py tests/test_research_costs.py tests/test_research_preparation.py tests/test_research_freeze_forward_audit.py --basetemp .pytest-conditional-v2 -p no:cacheprovider
    .\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-conditional-v2-all -p no:cacheprovider
    git diff --check

実データの完了条件は要求期間・利用可能期間・除外・Funding coverageが明記され、8通り×profileの比較、候補A〜E、hold別、月別、方向別、regime別、感度、非重複口座が同じ入力hashへ辿れること。採用不成立でも研究処理は完了し得る。採用成立は別条件であり、未完データや単一最良閾値だけではv002を有効化しない。

## Idempotence and Recovery


v001、既存strategy、既存分析artifactは上書きしない。新出力は一時ファイルから原子的に確定し、manifest完成前の結果は下流で拒否する。同じ入力/config/code hashの完了artifactは検証後に再利用し、不完全ファイルは同じrunの一時領域から作り直す。中断時に外部送信は存在しないため二重注文や課金は発生しない。研究設定の変更は新runとして保存する。

## Artifacts and Notes


実装は`conditional_edge.py`、`conditional_report.py`、`conditional_replay.py`と3つのCLIへ追加した。実行artifactはGit管理外の`data/research/conditional-edge-v2/`にあり、features 105,107行、labels 1,681,920行（complete 1,681,664、incomplete_price 256）をmanifestで固定した。価格SHA-256は`1832caee2dc69ff72f8545e0588f3e9de26da811478bd8ecc535f81b8e301dc6`、Funding SHA-256は`d87d8171e101d0d9259126bae0752ec750fcac6eb8870903eff660a3009cd67a`である。`freeze.json`はcode commit `42b493d4483d84175f7f3348201055bcef0b1335`と10個の入力/出力artifactを固定し、fingerprintは`6bdb161685a4cb54ff69ecc1b795b59821a69eb39e4dd0303a5b93eba9cecc7b`である。

## Outcomes & Retrospective


2026-09-15：M1〜M5を実装・実行した。1日、1か月、365日の順で同じCLIを確認し、LLM/Batch/注文を呼ばなかった。365日ではD/Q75/固定SL-TPの5/10/15/30分候補が各716〜822取引で約25% DDへ到達し、net PnLは約-249〜-250 USDだった。品質は境界不足を含む`partial`、結論は`inconclusive`で、v002を作らないことが採否契約に沿う結果となった。

2026-09-17：監査用の再集計では、88候補すべてがexploration・validation・confirmationの各foldでnet PnL負だった。選出4候補は検証期間で1,570取引、LONG 741・SHORT 829と件数条件は満たしたが、2026-03〜06の4か月すべてが負、両方向net PnLも負で、採用候補はゼロだった。年間の各別口座Replayは2025-12-01〜04に25% DDへ到達して停止し、最終的に716〜822取引、net PnLは-249.01〜-250.13 USDだった。

5分・10分候補の約定調整前価格PnLは+17.12 USD、+4.28 USDだったが、spread/slippage各約-41.10/-39.15 USDと手数料-184.94/-176.16 USDを回収できなかった。15分・30分候補は価格PnL自体も-16.36/-15.10 USDだった。Fundingは各候補で-0.02〜+0.05 USD程度で主因ではない。従って、現候補群は費用を除いても一貫した方向優位を示さず、v002を作らずこの候補群を不採用で閉じる判断を維持する。

実装の対象回帰として`tests/test_research_conditional_edge.py`、`test_research_conditional_replay.py`、`test_research_conditional_report.py`を実行し11件通過した。全178件のpytestは、実行後にpytestがtemporary directoryを走査する段階でWindows `WinError 5`となる既存のACL制約で完走できなかった。コンパイルと`git diff --check`は別途通過した。このACLは研究ロジック・成果物生成の失敗とは扱わず、別環境で全件を再確認する。

変更履歴：2026-09-15 初版。ユーザーのInitial Strategy再設計方針v2に基づき、既存v3のStage 1/2だけを詳細化した。
