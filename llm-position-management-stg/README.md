# 継続保有型BTC bot：初期実装

Hyperliquid Mainnetの公開データで仮想売買し、Geminiの判断・継続保有・費用を記録する。Cloud Runへ配備するHTTPアプリ、Firestore永続化、管理CLI、Testnet専用の注文検証経路を含む。実資金を使う注文経路はない。

この版は接続前の初期実装である。GCPプロジェクト作成、実Gemini・Firestore・Discord・Hyperliquidへの接続、Testnet実注文、30日評価は未実施。オフラインのテスト成功は、これらの成功や収益性を示さない。

[Gemini 3.6 Flash移行・料金・旧実験の扱い・検証範囲](../docs/gemini-36-migration-20261011.md)

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

トレーダーは`TRADER_MODEL=gemini-3.6-flash`、レビュアーは`REVIEWER_MODEL=gemini-3.8-flash`。応答期限は60秒／180秒、通常処理の目安は270秒。開始遅延60秒超ではトレーダー判断を省き、照合・保護は続ける。応答中に0.3%以上価格が変わるとリスク増加を止める。SLや強制縮小で建玉バージョンが変わった応答は破棄する。レビューには保存の20秒余裕を要求し、不足時は次枠に延期する。

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

Testnet専用の口座・別BOT_ID・別状態保存先を使う。`testnet-tick`は明示した1判断を検証するCLIで、30日の収益評価経路とは独立する。継続自動運転やGeminiへの直接接続はこの経路ではまだ行わない。既存Testnet接続部品と同じ公式SDKを使い、Mainnetの秘密鍵や発注先を受け付けない。

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
