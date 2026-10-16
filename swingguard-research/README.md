# SwingGuard-inspired price-only adaptation

日次BTC/ETH配分生成と5分リスク管理を分離し、生成前にも口座riskを渡す方式を比較する。原典の完全再現ではなく、ニュース/onchain/macro等を価格特徴量と決定論的ルールに置き換えた現物価格適応版。今回の結果にGemini/LLMは使用していない。実取引・資金操作の実装はない。

[結果報告](reports/report_ja.md)、[全指標](reports/summary.json)、[v1/v2差分CSV](reports/v1_v2_metrics.csv)、[入力出典/hash](reports/data_manifest.json) を公開する。詳細台帳、生データ、cache、環境調査はGitに含めない。

## 新規checkoutからの実行

Python>=3.10。rule版とテストは標準ライブラリのみ。repo rootのPowerShellで実行する。downloadだけがBinanceの公開配布URLへ接続し、認証は不要。既存の同じoutputは上書きしないため、再実行時は未使用名にする。

```powershell
$env:PYTHONPATH='swingguard-research/src'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s swingguard-research/tests -v
python -u -m swingguard.cli download --config swingguard-research/configs/default.json
python -u -m swingguard.cli run --config swingguard-research/configs/default.json --output swingguard-research/results/reproduce --cache-root swingguard-research/data/reproduce-cache
python swingguard-research/verify_results.py --output swingguard-research/results/reproduce
```

既存 `binance-btcusdt-futures-research/download_binance_klines.py` の取得/CHECKSUM helperを利用するため、リポジトリ全体をcheckoutする。BTCUSDT/ETHUSDT各122,688本、UTC2024-06-01以上〜2025-08-01未満の現物5分足。28の月次ZIPの配布SHA-256、ms/us、OHLC、連続性と銘柄間整合を検証し、欠損補間をしない。正規化先は `swingguard-research/data/`。

この同じ入力を [EvoQuantLab適応](../evoquantlab-research/) と [ExitRL適応](../exit-policy-research/) が読み取りで共有する。CSV hashは公開manifestと照合する。

## 比較と固定条件

原典期間は2024-08-01以上〜2025-02-01未満の184日、後続期間は2025-02-01以上〜2025-08-01未満の181日。先行60日以上をwarmupに使い、各期間/方式は10,000 USDT、建玉なし、NORMALで開始する。後続期間はこの適応の調整には使っていないが、過去研究やモデル学習でも未使用とは保証しない。

| 方式 | 日次生成への入力 / 制御 |
| --- | --- |
| pre_risk | 市場特徴量と自口座riskを渡し、生成後にもcontrollerを適用 |
| post_only | 市場特徴量だけを渡し、生成後にcontrollerを適用 |
| uncontrolled | post_onlyと同一市場入力・proposal cacheを共有し、4状態controllerを無効化。SL/TPは共通 |
| buy_hold | 初日に50/50購入を指示し、SL/TP・日次リバランスなしで期末決済 |

offline ruleは通常時に終値>SMA20かつ7日return>0、非NORMAL時に終値>SMA60かつ7日return>1.5%、SURVIVALでは見送りとする。SMA60への変更が常に厳しい条件になるわけではなく、riskに応じた別のtrend条件である。pre/postは自口座riskによる閉ループ比較であり、同一risk系列の提示順だけを変えた実験ではない。

数量は確定足までの資産・直前終値で固定し、次の利用可能な5分始値で約定する。遅延応答は受信後最初の始値へ繰り越す。共同weightは `max(1, BTC+ETH)` で正規化し、売却後の現金不足は買付を比例縮小する。commissionは片道10bp、slippageは片道5bpを約定価格へ反映する。現金+端数量のspot相当で、空売り・借入・実取引所への注文経路はない。

SL8%、TP16%。gap stopは不利な始値、gap TPはtarget価格、同足両到達はSL優先。足内の正確な到達時刻は不明と記録する。初回fillが価格アンカーで、追加購入時のSLは `max(従来stop, 新平均取得価格×0.92)`、TPは初回fill×1.16を維持する。部分売却でもアンカーを維持し、flat後の新規建玉で更新する。日次更新でSL/TPや保有時間をリセットせず、固定最大保有時間は設けない。保護決済後の再購入は、その後の正常proposalまで禁止する。期末は最終確定終値で全方式に売却費用を課す。

NORMAL/ALERT/SURVIVAL/RECOVERYの配分上限は1/.5/0/段階.25,.5,.75,1。ALERTはepisode DD4%・日次損失2%・最大銘柄1h下落3%のいずれか、SURVIVALは12%/6%/8%。最低12h待機後、日次損失<1%かつ1h下落<1.5%で復帰開始、静穏な24hごとに段階復帰する。復帰中3%再下落で再停止する。復帰完了時にepisode高値を更新するが、成績DDの全期間高値は維持する。12%は全期間DDの保証ではなく、0.5ポイントのdrift許容と次open補正のためcapは足内・連続時間の硬上限でもない。

## v2修正の契約

最新確定足をその足が属する日の資産基準で一度だけ判定してから、新日の基準を更新する。00:00の `daily_loss` / `completed_day_loss` は直前完了日、`current_day_loss` は新当日0。前日23:55足の損失をresetで消さず、境界でcontrollerを二重更新しない。

新規注文のproposal期限は24h。日次更新が始まった場合のみ旧建玉に最大15分の猶予を与え、最大stalenessは24h15mとする。猶予中は買い増し・再購入を禁止し、risk縮小・SL/TPは有効。正常応答は受信後最初のopenで採用、不正応答は受信時、無応答/許容遅延超過は固定deadlineでflatにする。許容遅延も15分で、deadlineちょうどの正常応答は採用する。待機中のstop lockを、その要求の応答で解除しない。15分は5分足3本分の固定運用仮定であり、成績を見て選んでいない。

## Gemini adapterと費用ガード

Gemini adapterは任意依存で、今回の保存成績には未使用。実APIのモデル利用可否・quota・請求は未検証。利用する変数名はGEMINI_API_KEYで、optional dependenciesはpyproject.tomlのgemini extraにある。資格情報をGitへ保存しない。

公開料金の2026-10-16時点snapshot（gemini-3.6-flash、input $0.75/output $3.75 per million、2026-12-31まで）を固定する。要求alias・返却名・料金は明示allowlistで照合し、未知の要求名はSDK呼出/予約保存前に拒否、未知の返却名は受信時に負債を保持して停止する。任意suffixのprefix一致を許可しない。価格期限を過ぎた新規送信も停止する。

予算は明示的に0超〜10 USD、単一attempt。request/受信応答/usage/latency/価格/proposalを原子的journalで管理し、永続受信後の中断は再送せず復旧する。結果不明は予約保持で停止し、別hashも迂回できない。token内訳とtotal、thinkingのゼロ推論条件、モデルと単価を検証する。同一journal内のローカル費用管理であり、他cache/アプリや供給側の制限違反を含む実invoice上限の保証ではない。

料金・モデルの根拠: [公式モデル文書](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)、[公式料金](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.6-flash)。後年のモデルで過去を評価すると、live retrievalを禁じても将来経路の記憶による汚染を排除できない。

## 検証と限界

既存66件とモデルallowlist回帰3件の計69テストを実施。日付境界、遅延/無応答/不正応答、SL/TP、stop lock、usage整合、journal各保存段階の中断と再開をmockで検証した。最終ruleの全8ケースは保存v2と32経済出力・全指標が一致し、元v2の新規cache再実行は34決定論的出力がbyte一致。独立台帳も420,480行/1,452約定を照合した。公開検証要約は [validation.json](reports/validation.json)。

各再実行はfreeze.json、artifact_manifest.json、summary.json、期間/方式ごとの取引・5分資産・提案・risk遷移を生成する。5分OHLCは足内順序、板、最小注文単位、変動slippage、市場impact、税を再現しない。2つの期間だけで統計的優位性や一般化は確立できず、価格ルール結果をLLM効果やHyperliquid/perp実績と呼ばない。

原典: [SwingGuard](https://arxiv.org/html/2610.12988v1)。
