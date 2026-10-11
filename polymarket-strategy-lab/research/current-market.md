# 現行Polymarketの仕様と履歴データ取得

確認日: 2026-10-04 UTC。各HTTP取得時刻、URL、レスポンスは `research/probes/`、本取得は `data/expanded_books/` に保存する。外部への注文・署名・送金は行っていない。

## 現行仕様を反映する条件

現行の手数料は `shares × rate × price × (1-price)` で計算する。公式資料の区分別rateはCrypto=0.07、Weather=0.05、Politics/Finance=0.04で、makerは無料、takerに適用される。旧リポジトリの固定bpsや旧指数付き曲線をそのまま使わない。市場別 `feesEnabled` と `feeSchedule` を保存し、現在の費用を使った評価と当時の損益を区別する。実際に取得した2026-08-28 BTC5m市場にもrate=0.07、exponent=1が含まれていた。[手数料](https://docs.polymarket.com/trading/fees)、[市場別設定](https://docs.polymarket.com/market-data/market-details)

表示価格や過去の約定価格だけでは、買付可能な価格と数量は分からない。taker買いはask、売りはbidを基準にし、板の数量と遅延を確認する。makerのキュー位置、報酬、約定率を観測せずに利益へ加算しない。[価格と板](https://docs.polymarket.com/concepts/prices-orderbook)

`closed=true` は閉鎖状態を示すが、それだけでは最終払戻しを確定できない。実際のbinary winnerはCLOB `/markets/{conditionId}` の `tokens[].winner`、解決状況はData API v2 `/resolutions` で確認できた。払戻しの0/1や50/50と、決済の遅延を区別する。市場の終了予定日時を資金回収日時に置き換えない。[解決](https://docs.polymarket.com/concepts/resolution)、[Resolution API](https://docs.polymarket.com/api-reference/markets/get-resolution-state)

tick sizeと最小注文数量は市場から読み取る。現行Place OrdersはCLOB `/book` の `min_order_size` をsharesと明記し、指値注文のrounded share quantityと比較するよう説明している。その株数の仕様を今回のtaker指値再生に適用する。一方、Market DetailsはGamma `orderMinSize` をUSDC建てと説明しており、公式SDKのGamma mappingにも価格換算はない。両資料の単位には不一致が残るため、独自の「最低5 USDC」を追加して同じ制約だと断定しない。実注文による受理検証は行っていない。[注文仕様](https://docs.polymarket.com/trading/place-orders)、[市場情報](https://docs.polymarket.com/market-data/market-details#trading-constraints)、[SDKのGamma mapping](https://github.com/Polymarket/py-sdk/blob/main/src/polymarket/models/gamma/market.py)

## 無認証APIを実HTTPで検証した結果

`urllib.request`、`ProxyHandler({})`、`User-Agent: Mozilla/5.0` で公開APIのHTTP 200を確認した。認証情報は使用していない。

| 経路 | 確認結果 | 用途・制約 |
|---|---|---|
| Gamma `/events?slug=...` | 過去のBTC5mと日次天候を取得 | BTC5mは `/markets?slug=...` が空でもevent内のmarketsから取得できた |
| CLOB `/prices-history?market=...&interval=max&fidelity=60` | 8月・9月の8 tokenすべてHTTP 200だが空配列 | 空配列を価格ゼロや不存在の証拠として使わない |
| Data API `/v2/prices-history?token_id=...&interval=max&bucket_seconds=10800` | 8月1日Chicago 68–69°Fで19点 | 3時間bucket。`resolution_seconds` の終端まで観測された可能性があり、bucket開始時の確定値として使わない |
| Data API `/v2/trades?condition=...&limit=1000` | 同市場の227約定を取得、`has_more=false` | 正確な約定時刻・価格はあるが、その時点の両側の板や今から出す注文の約定可能性は証明しない |
| Data API `/v2/resolutions?condition=...` | 同市場でresolved、最終price、transaction hashを確認 | 当時の意思決定特徴には加えず、払戻しラベル専用にする |

上表の天候市場のcondition IDは `0x2f3121a9b0eea9f1cc22429d4dd2463e7f0bb07e8f16c080c2b5cfe5bb3e8a77`。227約定の観測範囲は2026-07-31 01:34:35～2026-08-01 20:16:12 UTCだった。`limit=100` の3ページは100+100+27件で、一括227件と完全一致した。

ページングでは元の `condition` フィルタと `limit` を保持し、`cursor` を追加する必要があった。cursorだけにしたリクエストは0件と `has_more=false` を返し、取得漏れになった。Data API v2の公式説明ではcondition/event単位の取引には3年の固定窓があり、`start`/`end` はuser単位の取得でのみ有効。taker-onlyを使い、maker側とtaker側の二重計上を避ける。[Trades API](https://docs.polymarket.com/api-reference/feeds/list-trades)、[履歴API](https://docs.polymarket.com/api-reference/markets/get-a-tokens-price-history)

Gammaの `order=volume` は文字列順になった実例がある。将来の最終出来高で市場を選ぶと選択バイアスも入るため、今回の拡張BTC標本は日付・UTC時間を先に固定し、当該時間帯の全12市場を対象にした。

## 板アーカイブの実測

PMXTの直接URLは今回の実HTTPではタイムアウトした。Pendulum FlowはPMXT旧データのmirrorと、独自v3アーカイブを公開しており、v3のHTTP Rangeを206で確認できた。認証不要、CC BY 4.0で、出典表記が必要。[Pendulum Flow](https://archive.pendulumflow.com/)、[v3形式](https://archive.pendulumflow.com/formats/v3)、[PMXT v2形式](https://archive.pendulumflow.com/formats/v2)

2026-08-28 16 UTCの元Parquetは1,349,437,324 byte。DuckDBのevent-type・market条件で部分取得し、16:00開始のBTC5m一市場についてbook=4,048、best_bid_ask=4,984、last_trade_price=1,869行を約3秒で数えた。保存した部分Parquetは898,791 byteであり、元の巨大ファイルを丸ごと取得する必要はなかった。HTTP接続にはMozilla User-Agent、明示的なCA bundle、proxy設定の除外が必要だった。証明書検証は有効にしている。

v3にはcollectorの `timestamp_received` とvenueの `timestamp` がある。複数collectorの統合データなので、一つの接続に届いた順序そのものは復元できない。受信時刻順に再生し、決定時刻より前の行だけを使用する。bookの数量はfull snapshotから取得し、BBOが一致しなくなったら次のsnapshotまで無効にする。省略したprice_changeを推測で補完しない。

本取得は2026-09-01～09-30の毎日16:00～17:00 UTC、計360市場を事前に固定した。要求は30日全時間の連続データではなく、30本の1時間区間である。実際の取得件数・欠損・ファイルSHAは `data/btc_expanded_replay.manifest.json` と日次manifestを参照する。

30区間すべての取得が完了し、360市場・107,849行を保存した。Binanceの実1秒足は事前120秒を含め111,600秒あり、欠損は0秒だった。両側の板が有効な行は71,880行で、さらに両側のfull snapshotの経過時間を1秒以下に制限すると67,677行となった。価格が変化しなくても数量は古くなるため、拡張評価ではsnapshotの鮮度も制限する。210ファイルのSHA、判断時刻より前の受信時刻、実際の終値との一致、60個の連続終値による平均を `verify_expanded_data.py` で検証し、結果を `data/btc_expanded_integrity.json` に保存した。

## BTC解決条件と外部価格の扱い

取得した2026-08-28のBTC5m市場の解決資料はChainlinkの `btc-usd-twap-60s-streams` を明示していた。市場説明には対象時間帯のTWAPと開始時価格を比較すると書かれている。Binanceの最後のスポット価格と開始価格だけを比較して、公式winnerを自作してはいけない。説明だけから対象時間全体の平均か、streamの終端TWAPかを独自に決めず、実際の払戻しには公式winnerを使用する。[当該event](https://gamma-api.polymarket.com/events?slug=btc-updown-5m-1787932800)、[指定されたChainlink stream](https://data.chain.link/streams/btc-usd-twap-60s-streams)

拡張評価の外部価格にはBinance公開APIの実際の1秒足終値を使う。足のclose時刻に1秒を加えた時刻を利用可能時刻と仮定し、それより後の決定でのみ参照する。60個の連続した実際の終値の平均を `binance_twap60_price` に保存する。これはChainlink oracleの代用品であり、実際のChainlink観測値として出力しない。欠けた秒をゼロや架空のquoteで埋めない。[Binance market data endpoint](https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1s&startTime=1788278400000&limit=3)

Telonexのmetadata datasetは無認証だが、取引・板の通常download例にはAPI keyが必要だった。今回は登録・有料契約をせず、上記公開APIとアーカイブを使用した。[Telonex SDK](https://telonex.io/docs/sdk/python)
