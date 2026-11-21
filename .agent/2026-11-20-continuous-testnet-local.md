# 継続保有版のTestnet常時運転とSQLite記録

このExecPlanはリポジトリルートの `PLANS.md` に従う。進捗、発見、判断、結果を実装と実接続の確認に合わせて更新する。

## Purpose / Big Picture


利用者は、BTC建玉を5分で強制決済する旧botではなく、5分ごとに保有・追加・縮小・決済を選ぶ継続保有botをローカルのバックグラウンドで試したい。Geminiのトレーダーは `gemini-3.6-flash`、レビュアーは `gemini-3.8-flash`。2時間ごとに蓄積した判断理由と実行結果から戦略データを更新し、固定の安全制約は変えない。Hyperliquid Testnetだけへ注文し、口座・注文・約定・理由・レビュー・利用量をSQLiteへ残す。後からCLIでサマリーを確認できる。Discord、Firestore、クラウド配備はこのローカル運転では使わない。

## Progress


- [x] (2026-11-21 23:01 JST) 旧プロセスを停止し、独立した初期戦略と2時間ごとの改善ループを実装した。全92テストと実Gemini APIでレビュー形式を確認した。
- [x] (2026-11-21 23:01 JST) 新版をバックグラウンド起動した。旧プロセスの終了、テストネットの建玉・未約定注文がゼロ、SQLiteへの `hourly-pullback` v1 の保存を確認した。
- [x] (2026-11-20) 利用者の意図を再確認し、誤って起動した旧5分保有版のPID 20888/2348を停止した。旧台帳は初回見送り、注文0件。
- [x] (2026-11-20) 既存TestnetController、SQLiteStore、Gemini、Risk、CLI、PLANS.mdを確認した。
- [x] (2026-11-20) Testnet接続・継続保護・応答後の照合を実装し、hold継続、SL喪失、二重起動、未知注文、部分約定をローカル検証した。
- [x] (2026-11-20) 常時判断と2時間レビュー、約定・Funding・利用量・サマリーをSQLiteに保存するようにした。
- [x] (2026-11-20) .env対応のローカルCLI、単一プロセス制御、停止・サマリー操作を実装した。
- [x] (2026-11-20) 73件のローカルテスト成功。Testnet初回枠で両モデルの応答、判断理由、実行結果、履歴をSQLiteへ保存した。
- [x] (2026-11-20 23:25 JST) 旧bot停止を確認し、継続保有版を非表示でバックグラウンド起動した。
- [x] (2026-11-21 06:15 JST) 方向別の売買条件、実行可能な建玉倍率、見送り理由の集計を追加した。75件のテストと過去79観測の再生を確認し、旧プロセスを終了して同一DBで非表示起動した。初回の新形式レビュー・判断・保存に成功した。
- [x] (2026-11-21 22:22 JST) 旧botを停止し、Testnetの建玉と未約定注文がゼロであることを確認した。旧コードを `old/v1`、停止時点のSQLiteを `llm-position-management-stg/data/testnet-live-old-v1.db` に保存した。
- [x] (2026-11-21 22:29 JST) ローカルTestnetの新しい判断・退出・レビューを実装し、全84件のテストと保存した観測の再生で検証した。

## Surprises & Discoveries


既存の `llm-position-management-stg` は継続保有の仮想運転と、JSONを1回適用するTestnet CLIまでを持っていた。旧 `hyperliquid-ai-trader run-local` は毎回cleanupするため今回の運転に使えない。旧botは停止時点でslot0がabstained、ordersが空だった。

実Testnet口座はUnified Accountで、公開口座情報とAgentの署名noopを確認済み。従来の新ControllerはPerpのaccountValueしか読まず、この口座のSpot USDC共有担保に対応していない。常時運転前に修正する。

モデルの存在確認と、新しい両モデルのJSON応答は成功済み。これは有料/無料ティアや将来のquotaを保証しない。費用は設定単価による推定として保存する。

## Decision Log


2026-11-20: 既存の継続保有パッケージを拡張し、旧botは起動しない。5分を保有上限ではなく判断間隔として扱う。Testnetへの新規・増減・反転・SLは既存Controllerを通す。

2026-11-20: 市場特徴量と発注口座をTestnetに揃える。Mainnetの価格とTestnetの約定価格の差を売買機会と誤認しない。既存shadowのMainnet取得は既定動作として維持する。

2026-11-20: ローカルCLIだけが明示的な .env を読み、既存の `HL_test_wallet` と `HL_test_wallet_private_key` を受け付ける。秘密はログ・DB・コミットへ保存しない。実行にはTestnet専用コマンドを使い、Mainnet設定を拒否する。

2026-11-20: 単一OSプロセスのファイルロックとSQLiteの比較更新、5分枠IDを併用する。モデル送信を記録してから呼び出し、不明な呼び出しは自動再送しない。SQLiteと市場状態の照合が済むまでリスクを増やさない。

2026-11-20: 15秒程度で保護確認し、LLMが動作しなくても既存SLと必要な縮小を管理する。通常終了では既存SLを残し、終了だけを理由に全決済しない。未知の既存建玉・外部注文は自動採用せず停止状態にする。

## Outcomes & Retrospective


11月21日23:01 JSTに、独立した `hourly-pullback` v1 でプロセスを起動した。互換用の口座・履歴は同じSQLiteに保存し、新しい系列のレビューデータは系列開始から集計する。実Geminiの出力形式は保存済みの市場データで1回確認した。ローカルテストでは初期戦略の適用、変更1件の採用、次回レビューでの採用結果の参照を再現した。初回の運転枠は23:10、初回の定期レビューは翌01:10の予定。起動時の23:00枠は開始期限を過ぎたため見送った。

11月21日22:07 JSTに、取引所のストップ注文2件が合計0.02377 BTCのショートを全決済した。停止後に口座を再照合し、建玉ゼロ・未約定注文ゼロを確認した。旧DBのSQLite整合性検査が通り、276判断枠、9約定、戦略v12までを保存した。旧記録の再生では、10分の境界にあった評価済み96枠のうち、フラットかつ両方向の条件が未成立だった27枠を識別した。固定退出条件は21:00の確定足で最初に成立した。14:25に提案されたスプレッド緩和では、保存された板と建玉の下で注文可能な数量に差が出なかった。新しいbotは停止中で、検証はオフラインで実施した。

11月21日06:15 JSTに戦略版5を生成し、Gemini両モデルとTestnetの照合が完了した。ロングは無効、ショートは直近6本の安値割れと最大10 bpsのスプレッドを条件に、純資産の0.5倍まで許可した。最初の枠は安値割れがなく、スプレッドも14.42 bpsで見送り。過去79判断の確定足と板に同じ新ルールを当てるとショートシグナル9回、うちスプレッド条件も満たす候補は2回。保存済み観測による条件判定の確認で、約定と運用損益は検証の対象外だった。旧記録は未評価のまま保持した。

実装は `llm-position-management-stg/.venv` のPython 3.12.16で検証した。旧研究の整理は4コミットをリモートmainへ統合し、リモートに既にあった清算計算の修正を保持した。

2026-11-20 23:25 JSTの初回枠で、Testnetで署名者の認可を確認し、両モデルの応答を受信した。戦略版1で見送りを選択し、理由と取引所の建玉ゼロをSQLiteへ保存した。初回枠はcomplete、エラーは0件。実注文の受理は次に売買を選ぶ枠で確認する。起動確認をもって完了とし、長期の運転成績は後からサマリーで確認する。

## Context and Orientation


`src/position_stg/testnet.py` は公式SDKのTestnet URLだけを使い、注文意図をSQLiteへ先に保存し、決定的なclient IDで重複注文を防ぐ。`domain.py` の `plan` は現在資産の3倍までの建玉、SLまでの追加損失3%、価格変動、DDを制限する。SLとは損切り注文で、プロセスが止まっていても取引所に残るreduce-only注文を使う。

`policy.py` のGeminiはTrader/ReviewerのJSON、使用token、応答時間、費用推定を保存する。`store.py` のSQLiteStoreはdocsテーブルにID/version/JSON本文を保存する。既存Controllerのruntimeと区別して、常時運転はlive-runtimeとlive-cycle文書を使う。既存 `runner.py` はshadow専用として維持する。

## Plan of Work


11月21日の追加では `initial_strategy.py` に、1時間足の方向と5分足SMA20の再テストを使う両方向の初期戦略を追加する。稼働中の旧プロセスを停止し、建玉ゼロを確認したら `run-testnet-local` の初回起動で独立した戦略系列を適用する。同じSQLiteで約定照合と旧履歴を保持し、新しい系列のレビューは系列開始以降の判断・約定を参照する。最新の実採用変更があれば次のレビューへ基準との差、費用控除後の観測値、評価限界を渡す。レビュアーの次の仮説・指標・採用と撤回の条件をSQLiteに残し、根拠を再現できる一つの変更を反映する。

11月21日夜の追加では、判断枠を10分にし、確定5分足のエントリー条件と約15秒の建玉保護を使う。買い・売りの条件をコードで検査して、候補のないフラット時は理由だけを保存する。保有時は建てた時点の退出ルールをSQLiteに保存し、逆方向の1時間足終値を検出したらLLMより先に決済を試みる。追加余地や新しい1時間足のない枠ではLLMを省く。レビュアーには過去の判断の板・建玉・発注制約を使った比較を渡し、根拠のある候補を採用する。コードと台帳を旧版と比較できるよう保存し、新版はオフラインで検証する。

11月21日の追加では、レビュアーがロング・ショートごとに5分足トリガー、相場条件、許容スプレッド、純資産に対する目標建玉倍率、無効化・再検討条件を指定する。トレーダーの呼び出し直前に同じ確定足と板で両方向を評価し、判断理由と一緒にSQLiteへ保存する。直近24枠の成立・見送り理由をレビュー入力とサマリーに含める。旧形式の戦略しかないDBは、新形式の初回レビューを待って移行する。検証後に testnet-stop で終了を確認し、同一DBで非表示起動する。

第一段階は実口座に合うTestnet接続と継続保護である。`TestnetExchange.snapshot` をUnified Accountへ対応させ、`TestnetController.maintain()` を追加する。この処理は注文結果・SL・外部変更を照合するが、5分判断枠を消費しない。`tick` は判断時の数量・価格と応答後の状態を比べ、古い判断で建玉を増やさない。

第二段階は `live.py` のLiveRunnerである。初回に有効なレビューを取得し、5分枠でTraderを呼び、2時間ごとにReviewerを呼ぶ。判断理由、実行結果、前後口座、戦略版、エラーを記録する。実約定とFundingを一意IDで保存し、同じデータの二重集計を防ぐ。モデルを呼ばない保護確認を繰り返し、異常時も適度な待機を入れる。

第三段階はCLIと運用手順である。`run-testnet-local` に .env とSQLite指定、任意の最大判断回数を用意する。無指定では運転を継続する。起動前にAgent認可、口座、モデル設定を確認し、Testnetレバレッジを明示する。別プロセスによる二重起動を拒否する。`testnet-summary` でSQLiteだけから記録を表示できるようにする。

最後にFake Exchangeで回帰を確認し、実Testnetで初回のレビューと判断、SQLite保存を確認する。常時運転プロセスはWindowsの非表示起動とし、PID・ログパスを記録する。利用者の指示どおり終了まで待たない。

## Concrete Steps


作業ディレクトリはリポジトリルート。テストは次で実行する。

    .\llm-position-management-stg\.venv\Scripts\python.exe -X utf8 -B -m pytest llm-position-management-stg/tests -q -p no:cacheprovider

バックグラウンド起動と同じCLIを前面でも実行できる。別のPowerShellからサマリーを読む。

    .\llm-position-management-stg\.venv\Scripts\position-stg.exe --env-file .env --sqlite llm-position-management-stg/data/testnet-live.db run-testnet-local
    .\llm-position-management-stg\.venv\Scripts\position-stg.exe --sqlite llm-position-management-stg/data/testnet-live.db testnet-summary

## Validation and Acceptance


5分を跨いだholdで建玉を閉じないこと、同じslotと再起動でLLM/注文が増えないこと、SL消失時に復元または未保護分だけ縮小すること、SL約定中の古い応答を拒否すること、Unified担保を読むことをテストする。Traderの理由とReviewer戦略がSQLiteへ残り、summaryから確認できることを検証する。APIエラー、注文拒否、Funding取得不足を成功損益として報告しない。

実起動の受入条件は旧runner0件、新runner1系統、初回処理が記録済み、Testnetにしか接続しないことである。LLMが見送りを選んだ場合は取引を強制せず、その理由が記録されたことを確認する。これは収益性を評価した結果ではない。

## Idempotence and Recovery


専用の新SQLiteを使い、既存shadow/旧botのDBを上書きしない。途中で止まった枠は未完了として残し、同じ有料呼び出しや不明注文を再送しない。再起動時は保存状態と取引所の建玉・注文を照合し、未知の差があれば新規取引を止める。停止中も取引所SLは残る。キーや設定の再編集はファイルの必要箇所だけに限定する。

## Artifacts and Notes


SQLite、ログ、PID、接続試験結果は既存のGit除外data配下に保存する。ソース・テスト・README・この計画はGitへ残す。モデル設定は.envのTRADER_MODEL=gemini-3.6-flash、REVIEWER_MODEL=gemini-3.8-flash。

## Interfaces and Dependencies


既存google-genai、hyperliquid-python-sdk、requests、SQLiteを用いる。.envはpython-dotenvで読み、ファイルロックは標準ライブラリで実装する。LiveRunnerはConfig、SQLiteStore、Testnet用Market、Gemini、TestnetControllerを受け取り、テストではFakeを注入する。Controllerのtickはallow_increase、observed_mark、expected_quantityを受け取り、maintainは判断を消費しない。

変更履歴: 2026-11-20、利用者の継続保有Testnet常時運転・SQLite保存・バックグラウンド起動の依頼に基づき作成。
