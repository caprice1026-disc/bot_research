# 継続保有型BTC bot

Hyperliquid Mainnetの公開データによる仮想売買と、ローカルで継続保有するTestnet常時運転を持つ。Geminiの判断・理由・戦略レビュー・実行結果を記録する。Cloud Run用HTTPアプリ、Firestore接続、管理CLIも含む。Mainnetの実資金を使う注文経路はない。

GCPへの配備と30日評価は未実施。ローカルTestnetの運転記録はshadowの収益比較と分ける。接続・注文・保護の成功は収益性の証明にはしない。

## ローカルTestnet常時運転

`run-testnet-local` は5分ごとに判断し、holdなら5分を越えて保有する。2時間ごとのレビューは相場仮説・売買ルール・失敗傾向を更新する。約15秒ごとに注文とSLを照合し、モデルの応答待ち中も保護確認を続ける。初回レビュー成功前、戦略期限切れ、期限超過応答、照合未完了時にはリスクを増やさない。ニュース・Discord・Firestoreはこの経路では使わない。

レビューではロングとショートそれぞれに、相場の方向、5分足の条件（SMA20の押し目・戻り、クロス、直近6本の高値・安値突破、SMA20の上下）、許容スプレッド、目標建玉倍率の上限、仮説が崩れる条件と再検討条件を指定する。倍率は純資産に対する建玉額で、設定上限以下の0.5倍刻みを使う。各判断で確定足とその時点の板から両方向の条件を評価する。条件が成立してもトレーダーは見送りを選べる。条件外の新規・追加は実行側で制限し、縮小・決済・SL管理を続ける。

SQLiteの各 `live-cycle-*` にある `entry_checks` で、方向別のシグナル成立、発注候補かどうか、見送りに該当した条件を確認できる。モデルの判断理由は `reason`、注文管理の結果は `execution` にある。サマリーの `entry_summary_last_24_cycles` は直近24枠を集計する。2時間レビューは直近2時間内の枠だけを使う。移行前の判断は未評価として数え、旧DBは再起動後の最初の有効な枠で新形式のレビューを行う。

市場特徴量も発注もTestnetのBTCを使用する。新規DBでの起動には建玉・注文のない専用口座を要求し、Agent認可と担保を確認する。Unified AccountのSpot USDC共有担保に対応する。フラットで起動する際に3倍isolatedへ設定し、数量は現在純資産3倍とSL追加損失3%の両方で制限する。未知の注文や説明できない建玉変更は停止状態とする。

リポジトリルートで実行する。新しい端末ではPython 3.12以上の仮想環境へ `pip install -e './llm-position-management-stg[test]'` で導入する。既存端末の環境は次のパス。

```powershell
$botPython = '.\llm-position-management-stg\.venv\Scripts\python.exe'
& $botPython -X utf8 -B -m position_stg.cli --env-file .env --sqlite llm-position-management-stg/data/testnet-live.db run-testnet-local
```

`.env` はこのコマンドで明示的に読み込む。`GEMINI_API_KEY`、`TRADER_MODEL=gemini-3.6-flash`、`REVIEWER_MODEL=gemini-3.8-flash`、`HL_test_wallet`、`HL_test_wallet_private_key` を使える。`TESTNET_ACCOUNT_ADDRESS`、`TESTNET_PRIVATE_KEY` という名前も受け付けるが、両方を指定するなら一致が必要。`BOT_ID` の既定は `btc-continuous-v1-testnet`、明示する場合も末尾を `-testnet` とする。`HL_NETWORK=mainnet` は拒否する。キーやアドレスはDB・起動ログへコピーしない。

無指定では常時運転する。有限の動作確認には末尾へ `--max-cycles 1` を付ける。開始が5分枠から60秒以上遅れている場合、その枠を見送って次の枠へ進む。別プロセスが同じDBを使用している場合は起動を拒否する。停止・再開には同じSQLiteと同じ設定を使い、進行中の枠や結果不明の有料呼び出しを再送しない。設定の指紋が変わったDBはそのまま再利用しない。

SQLiteだけからサマリーを読み、後で判断理由と戦略を確認できる。

```powershell
& $botPython -X utf8 -B -m position_stg.cli --sqlite llm-position-management-stg/data/testnet-live.db testnet-summary
& $botPython -X utf8 -B -m position_stg.cli --sqlite llm-position-management-stg/data/testnet-live.db testnet-stop
```

`testnet-stop` は次の安全な区切りでプロセスを終了させる要求で、全決済の指示ではない。取引所に受理されたSLは残す。Windowsのバックグラウンド起動には `Start-Process -WindowStyle Hidden` とstdout/stderrのファイル出力を使う。PCのスリープ・再起動中はbotが動かないため、再開時に同じDBから照合する。

DBの `docs` テーブルには、`runtime`（注文管理）、`live-runtime`（戦略と処理位置）、`live-cycle-*`（判断理由・提案・前後状態・適用結果）、`llm-live-*`（モデル入力・応答・token・応答時間・費用推定）、`intent-*`（送信前の注文意図）、`live-fill-*`、`live-funding-*`、`live-account-*` を保存する。サマリーは口座評価差、観測した約定損益・手数料・Funding、API判明分と不明予約額を区別する。入出金と含み損益を分離した完全会計や、取引所履歴の無期限保存を保証しない。取得不足・不明応答をゼロ損失や成功取引として扱わない。

## 起動とオフライン確認

リポジトリルートで既存Python環境を使う。

```powershell
.\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pip install -e './llm-position-management-stg[test]'
$stgTestTemp = Join-Path $env:TEMP ('stg-' + [guid]::NewGuid().ToString('N'))
.\hyperliquid-ai-trader\.venv\Scripts\python.exe -m pytest llm-position-management-stg/tests -q -p no:cacheprovider --basetemp $stgTestTemp
.\hyperliquid-ai-trader\.venv\Scripts\position-stg.exe --sqlite llm-position-management-stg/data/fixture.db fixture
```

fixtureは外部通信せず、初回レビュー、新規、hold、追加、縮小、全決済と同一枠再送を確認する。既存DBを上書きしないため、再実行には別のDB名を指定する。`fixture_complete`は動作確認であり、収益評価結果ではない。

設定一覧は[.env.example](.env.example)にある。環境変数を設定して起動する方式で、`.env`を自動ロードしない。秘密情報はSecret Managerかプロセス環境から渡す。モデル変更後は実際のAPI利用可否、構造化応答、トークン上限と期限を接続検証する。

## 合意した運用契約

LLM・現金・BTC1倍保有・固定トレンドの4口座は各500 USDCで開始する。30日で時価評価し、保有と運用を継続する。初期は学習・モデル更新を実行せず、学習用の入力・出力・費用・状態を記録する。固定トレンドは確定1時間足のSMA20/50、方向に1倍、ATR14の2倍幅のSLで、LLMと同じRiskを適用する。BTC保有は市場比較用のperpetual口座であり、現物BTCの保有とはFundingが異なる。

LLMは5分ごとにhold、または純資産の0、±0.5、±1、±1.5、±2、±2.5、±3倍を提案する。holdでもSLを変更できる。SL拡大は許容し、必要な縮小を先に行う。建玉3倍と現在価格からSLまでの追加損失3%を同時に適用する。手数料と想定価格影響を予算へ含め、数量は切り下げる。通常差分が純資産5%未満なら売買を省くが、安全縮小・全決済は省かない。反転は決済後に再計算する。

区間DD10%で新規・追加・反転の新規側を止め、SL管理と縮小を続ける。24時間以上経過し、フラット・データ正常・手動停止なしなら区間高水位を更新する。全期間の高水位・最大DDは保持する。30日評価のリスク条件は最大DD15%以内で、超過しても運用は継続する。別の日次・時間損失上限は置かない。

トレーダーは`TRADER_MODEL=gemini-3.5-flash`、レビュアーは`REVIEWER_MODEL=gemini-3.8-flash`。応答期限は60秒／180秒、通常処理の目安は270秒。開始遅延60秒超ではトレーダー判断を省き、照合・保護は続ける。応答中に0.3%以上価格が変わるとリスク増加を止める。SLや強制縮小で建玉バージョンが変わった応答は破棄する。レビューには保存の20秒余裕を要求し、不足時は次枠に延期する。

レビュアーは2時間ごとに同じ処理の最後で動く。PANews RSSだけをその時点で取得し、記事ID／URLで重複を除く。公開時刻不明は不明のまま渡す。取得障害はニュースなしと区別して記録し、市場情報でレビューを継続する。初回は有効なレビュー成功を待つ。戦略の有効期限は6時間。Risk、口座、発注権限、停止解除はレビューの変更対象にしない。

月20 USDは通知目安で、初期の`LLM_BUDGET_ACTION=notify_only`は利用継続。`stop`なら最大出力を含む費用見積もりを予約できない呼び出しを止める。無料ティアは実プロジェクトで確認して`BILLING_TIER=free`を指定する。有料単価は環境変数で管理し、既定は保守的な標準単価。使用量、思考トークン、応答時間、受信モデル、推定費用を記録し、タイムアウト・不明な応答を正常応答と混ぜない。予算の月区切りはUTC。

## 市場データと評価上の制約

Mainnetの確定5分・1時間・4時間足を各60本取得し、リターン、ATR14、SMA20/50と元足を渡す。価格・板・Funding、口座状態、最新戦略、直近判断も入力する。APIが返した足の連続性と値を検証し、未来の未確定足を特徴量に使わない。

新規約定は応答後の板を数量分たどる。SLは前回以降の確定1分足で再現し、価格飛びではSL価格より不利な始値を使う。Fundingの決済markが公開履歴にないため、不利な足内価格で近似する。FundingとSLが衝突する場合は支払いを先に、受取りを省いて評価する。これらの近似をイベントに残す。

1分足の途中で数量やSLを変えると、足内の値動きとの順序が確定できない場合がある。変更前のSLを保存し、SL到達の可能性がある区間は保守的に処理して`partial`にする。欠損や1時間超の停止も`partial`とし、対象口座のリスク増加を止める。欠損を推測で埋めて評価を成功にしない。この版では欠損口座の完全復元を自動で行わず、新しいBOT_IDで別評価を始めるか、保存データを使って原因を調べる。

最大DDは観測・再生した価格系列上の値で、tick単位の真の最大DDではない。30日目の結果は`evaluation_30d`へ保持し、強制決済しない。クラウド請求が未確定、設定変更あり、取引不足、partialの場合は無条件に収益性合格としない。必要取引件数の統計的基準はまだ確定していないため、現在の結果は機械的な収益性採用判定を行わない。

## 永続化と通知

本番はFirestore。SQLiteはローカル検証用で、Cloud Runのローカルファイルを正本にしない。`bots/{BOT_ID}/documents`配下にruntime、cycle、LLM呼び出し、月予算、通知等を別文書で保存する。runtimeは比較更新と300秒の実行権を持ち、同じ枠を再処理しない。外部通信はDBトランザクションの外で行う。口座と再生位置と監査イベントは同時に保存する。

Discordへ異常・復旧を通知し、日本時間前日0〜24時の日次報告を翌9時以降に送る。日次損益には0時の1分足終値による境界評価を使用し、境界がない日はサンプル間損益と明記する。LLM費用は推定、クラウド費用は未取得なら不明のまま表示する。Discord送信前に送信意図を保存する。応答喪失時は`unknown`として自動再送せず、チャンネルを確認してから運用者が対応する。この方針は重複防止を優先し、通知の必達を保証しない。

```powershell
# GCP Application Default Credentialsで認証済みの端末から実行
position-stg status
position-stg pause
position-stg resume
position-stg report --day 2026-09-21
position-stg cloud-cost --day 2026-09-21 --usd 0.05 --source estimate
position-stg archive --bucket YOUR_ARCHIVE_BUCKET
```

`pause`でも保護処理は続く。`resume`はデータ欠損が残る口座の制限を解除しない。archiveは30日より古い確定履歴をJSONとしてStorageへ送り、SHA-256を再取得で照合した後だけFirestoreから削除する。runtime・未確定通知・未解決注文は残す。Storageの自動削除期限は設定しない。初期版のarchiveは管理CLIで実行するため、定期実行の設定は配備時に行う。

## Testnetでの注文機能検証

Testnet専用の口座・別BOT_ID・別状態保存先を使う。`testnet-tick`は明示した1判断を検証するCLIで、30日の収益評価経路とは独立する。この単発コマンド自体はGeminiへ接続しない。継続自動運転には上の `run-testnet-local` を使う。既存Testnet接続部品と同じ公式SDKを使い、Mainnetの発注先を受け付けない。

```powershell
$env:BOT_ID = 'btc-validation-testnet'
$env:ENABLE_TESTNET_ORDERS = 'true'
# TESTNET_PRIVATE_KEYとTESTNET_ACCOUNT_ADDRESSは安全な方法で環境へ設定する
position-stg testnet-tick --decision path/to/decision.json
```

入力例は`{"intent":"set_target","target_multiple":"0.5","stop_price":"50000","reason":"test scenario"}`。SL価格は実際の現在価格に対して有効な値へ変更する。固定例をそのまま本番注文に使わない。Testnetの証拠金・レバレッジ設定はこのCLIでは変更しない。

注文意図と決定的なclient IDを先に保存し、送信結果不明は照合する。SLの再設置を2回・20秒以内で試し、未保護分だけreduce-onlyで閉じる。DB障害直後でも取引所を再確認して未保護分を緊急決済する。説明できない数量・外部注文の変更は手動停止する。約定照合にはAPI履歴の保存範囲による限界があるため、長期停止後は手動確認が必要になる。API切断中は決済成功と報告しない。

## 東京リージョンへ配備する準備

新規プロジェクトで課金を有効にし、Cloud Run、Cloud Build、Artifact Registry、Firestore、Cloud Scheduler、Secret Manager、Cloud Storage APIを有効にする。FirestoreはStandard / Native、`(default)`、`asia-northeast1`で作成する。Artifact Registryは同リージョンに`position-stg`というDockerリポジトリ、Storageは同リージョンに専用バケットを作る。Storageライフサイクル削除は指定しない。

実行用サービスアカウントにはFirestoreの必要な読み書き、対象Secretの参照権限を付ける。退避CLIを別の管理者認証で動かすなら、Cloud RunへStorage削除権限を付ける必要はない。Scheduler用アカウントには対象Cloud RunサービスのInvokerだけを付ける。GitHub Actions用アカウントにはビルド・配備・対象サービスアカウントの利用・Scheduler更新に必要な権限を付け、Workload Identity Federationを使う。長期秘密鍵をGitHubへ登録しない。

Secret Managerに`gemini-api-key`と`discord-webhook`を登録し、GitHubの`stg` Environmentへ`GCP_PROJECT_ID`、`GCP_RUNTIME_SA`、`GCP_SCHEDULER_SA`、`GCP_DEPLOY_SA`、`GCP_WORKLOAD_IDENTITY_PROVIDER`、任意の`BOT_ID`を設定する。[workflow](../.github/workflows/position-stg.yml)を手動実行し、deployを選ぶとテスト後に配備する。既存の環境変数は`--update-env-vars`で維持する。モデルや閾値の変更はCloud Run環境変数で行う。

Cloud RunはIAM認証必須、1 vCPU・512MiB、min0/max1、concurrency1、timeout290秒。SchedulerはOIDCで`/tick`を5分ごとに呼び、実行予定時刻のヘッダーから枠IDを決める。HTTPハンドラー内のヘッダー検査は認証の代用ではないため、公開アクセスを許可しない。Cloud Runを東京に置いても、外部Gemini APIやHyperliquid側の処理場所を東京に固定したことにはならない。

最初のクラウド確認では、`/health`、1回のレビュー、次枠の判断、同枠再送、ログ、Discord、停止・再開、退避の順に確認する。実際の権限・OIDC・料金・SL注文の受理はローカルテストでは証明できない。デプロイ後はCloud Runのリビジョン、イメージcommit、Scheduler状態を記録する。
