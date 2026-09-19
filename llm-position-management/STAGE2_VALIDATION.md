# Funding精度・評価指標・Gemini canary・固定比較

2026-09-19実施。これは取引所へ発注しない履歴Replayである。API費用はユーザー指定10〜15 USDの下限10 USDを全試行共通上限とした。送信前にSQLiteのトランザクションで予約し、結果不明の費用は解放しない。実請求額ではなく、provider usageと公開Standard単価から推定する。

## Fundingと執行の時刻契約

共有coreの公開データreaderがBTCUSDTの確定1分足とFunding historyを取得する。Fundingは取引所の `fundingTime` と `markPrice` をそのまま台帳に残し、rate×その時点の保有数量×settlement markで計上する。新規建てがFunding時刻以後なら課さない。取得元は[Binance Funding history API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)。

1分足を処理した後の境界にFundingがある場合は、その足に属するSLを先に処理し、その後Funding、最後にAIの差分注文とする。Fundingが足の途中にあり、その足でSLまたは保有期限決済も発生する場合、OHLCだけでは前後関係を確定できない。この場合は `funding_stop_ambiguous` を残し、支払い側は決済前に課金、受取側は決済後として受取を計上しない不利側の評価にする。レポート品質はpartialとなる。これは実約定順序を復元したという主張ではなく、曖昧さを隠さないための契約である。

AI応答の遅延は実測し、Replay時計に加算する。執行は受信時刻以後の最初の確定1分tickのcloseを基準にspread/slippageを加える。実際の板約定価格や秒単位のSL時刻までは再現しない。将来の足を特徴量へ渡さず、完了した足の5/15/60分returnと直近closeだけを使う。

## 指標と対照

独立したイベント台帳からequityを再計算し、Simulator最終残高と一致させる。指標は費用込みPnL、API費用控除後PnL、1分刻み含み損込みDDとAPI込みDD、flat-to-flat保有時間、完了取引数、絶対約定notional合計のturnover、fee・spread・slippage・純Funding・APIの費用分解である。純Fundingは受取なら費用が負になる。既にPnLに含まれたspread/slippageを再度PnLから引かない。

4群は独立した1,000 USDT口座、250 USD anchor、片道fee 0.045%、spread 2 bps、slippage 1 bpを使用する。対照はno-trade、6時間周期の固定script。LLM群は増減なし（flat時の新規、保有中の維持か全決済）と増減あり（離散target ±1/±0.5/±0.25/0）を比較する。終端の5分前に決済指示を出す条件を共通化した。DDは分足評価であり、秒以下の最大逆行ではない。

## canaryとschema修正

モデルはGemini 3.5 Flash、minimal thinking、temperature 0、出力上限512 tokens。単価は[Google公式料金表](https://ai.google.dev/gemini-api/docs/pricing)のStandard入力1.50 USD／100万tokens、出力9 USD／100万tokens（thinkingを含む）。検索や外部実行toolは使わず、SDKの自動retryも無効である。

最初の環境参照エラーrunにはAPI送信・課金がない。次のcanaryは11応答すべて有効であり、同じSQLiteからの再開でも追加送信なしで判断・イベント・equity・reportのhashが一致した。しかし9月2日の比較前半で、hold応答に非null target/stopを含めるschema上の欠落が判明した。厳格なローカル検証が拒否し、安全決済も作動した。このcohortはhalf-dayのpartialとして保管し、戦略の優劣判定から除外する。

schemaをhold／flat／nonzeroのanyOfへ分け、保有中の増減なし群はhold／flatのみとした。schemaの全variantとpromptのhashをfreezeに追加した。既存応答を都合よく書き換えず、修正後canaryを再実行し、11件すべて有効、無効応答0、実測遅延1,548〜2,274 ms、推定費用0.0281385 USDを確認した。API送信後クラッシュは再送せず通常判断を凍結し、残存建玉を安全決済する。通信障害・重複・予算不足・3連続無効応答は別途オフラインテストで確認した。

実行は2026-09-20へ跨いだ。修正後比較中にも長時間待機が観測され、両群で1件ずつ通信結果不明と約61分のstale応答が生じた。stale応答は執行せず、結果不明の2件は自動再送せず0.05 USDずつの予約を残した。実行環境停止を含む可能性があり、APIサービスのみの遅延とは断定しない。このため全期間を完走しても運用品質はpartialとし、問題のあった区間を結果から都合よく除外しない。

## 固定比較の解釈

修正後の評価期間は2026-09-03 00:00〜2026-09-04 00:00 UTC。以前の研究は2026-08-31まで、先行cohortは9月2日までだったため、このリポジトリの当該研究で未評価の期間を使用した。モデルの学習データにも未使用であることは保証できない。公開データの全体hashは `123b7054db019585f86f55c3e064b198cd580262c0401b8f51afd014cf8a4009`。取得3日分は4,320本・Funding 9件で、そのうち比較対象は1日分。前日は特徴量のwarm-upにのみ使う。

結果を見た売買閾値調整は行わない。schema修正は別cohortと別期間として分離し、先行分も同じ10 USD予算台帳に含める。1日の比較だけでは増減機能の有効性・将来収益性を採否判定できず、研究結論はinconclusiveとする。

生データ・応答・SQLiteはGit除外し、`data/comparison-20260903-v3/` にfreeze、群別report/decisions/events/equityを保存する。共通API台帳は先行cohortを含む `data/comparison-20260902-v2/api/` にある。元のv001 active rulesやReviewerは変更しない。

## 最終結果（2026-09-20）

| 群 | 費用込みPnL | API判明分控除後PnL | API込み最大DD | turnover | 約定 | 追加 / 縮小 | 品質 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| no-trade | 0.0000 | 0.0000 | 0% | 0.00 | 0 | 0 / 0 | ok |
| 固定script | +9.6845 | +9.6845 | 0.4239% | 1,988.84 | 16 | 4 / 4 | ok |
| LLM増減なし | +10.2862 | +9.5633 | 0.3786% | 1,001.31 | 4 | 0 / 0 | partial |
| LLM増減あり | +5.3941 | +4.6599 | 0.1921% | 500.70 | 4 | 0 / 0 | partial |

金額は取引口座がUSDT、APIはUSDの1:1近似で集計した。各LLM群には結果不明API呼出1件があり、それぞれ最大0.05 USDの予約を別途残す。判明分にこれらを足した費用上限を管理するが、上表のPnL/DDへ架空の実請求は計上しない。spread/slippageは約定価格へ織り込んだモデル値である。

4群すべて1,440本の比較対象tickを処理し、`period_complete=true`、欠損なし、Funding/SL順序曖昧0件、最終建玉ゼロ、安全決済待ちなし。対照群は288判断、LLM群は276判断（長時間応答待機中の12slotは新規呼出をしない）。修正後LLM群のJSON/schema無効応答は0件、各群のstale 1件・結果不明1件によりqualityをpartialとした。LLMの保有時間中央値は増減なし686分、増減あり706分。5分ごとの強制決済ではなく継続保有が機能している。

増減あり群は新規時に小さいロットを選んだが、既存建玉への追加・縮小は0回だった。したがって、この差から追加・縮小機能の優位性は評価できない。小さいロットに伴いturnoverとDDも小さいが、利益も小さい。単一日の少数取引に加えて運用上のpartialがあるため、v001への採用や本番移行の根拠にしない。

全先行試行・canaryを含めたAPI試行は858件（usage確定856件、結果不明2件）。推定費用は **2.2572615 USD**、不明分予約 **0.10 USD**、合計管理上限 **2.3572615 USD** で、承認された10 USD以内。`summary.json`へ総費用と比較結果を保存した。全5run（canaryと4群）を保存状態から再評価し、判断・events・equity・reportのhashが一致し、API試行件数が増えないことを確認した。

オフライン回帰は新core/位置管理80件、旧Hyperliquid185件、合計265件成功。`compileall` と `git diff --check` も成功した。Testnet、Mainnet、Reviewer更新は実施していない。
