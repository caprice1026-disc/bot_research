# JPYC集中流動性と USD/JPY ヘッジの検討

調査時点: 2026-10-20 15:54–15:55 UTC。これは実取引やLPバックテストではなく、構造説明と公開市場データの確認。

## 結論
集中流動性LPとFXヘッジは技術的に可能。ただしLPは裁定者に流動性を提供する側なので、手数料収入だけでは収益性を判断できない。JPYC/USDCは同一通貨のステーブル同士ではなく、円ドルの為替変動を持つ。狭いレンジほど資金効率は上がる一方で、在庫偏り・レンジ外・再配置・ヘッジ頻度・adverse selection/LVRが増える。

JPYCの割安側にUSDC片側レンジを置き、JPYCを受け取ったらLPを外して1円償還する方法は、両側LP放置より今回の目的に近い『指値的な取得＋償還』。ただしLPは永続的な指値約定ではなく、放置して価格が反転すれば在庫は逆交換される。レンジ外は原則手数料が発生しない。利確には引出し、償還、FX参照更新などが必要。

## Hyperliquid公開API実測
- 市場: `xyz:JPY`、onlyIsolated=true、maxLeverage=50（推奨レバレッジではない）。
- oracle158.18、mark158.10。USDJPY形の指数水準。JPYという名称から円高ロングと誤解しない。
- 24h出来高約$2,364,894、OI192,158.94 units×oracle158.18≈$30,395,701。
- 2026-10-20 15:54:24.219 UTCの板: bid158.10/ask158.11、スプレッド約0.6325bps。最良bid約$3030.78、ask約$4743.30。100/1000USDの小口は当該瞬間の最良気配だけで数量が足りる。約定・未来の流動性保証ではない。
- funding直近24時間(Oct19 16:00〜Oct20 15:00UTC)率合計−0.04575531%。ショート側支払。一定1000USDC想定元本なら約$0.458/日。
- 7日168回率合計−0.18075259%、日平均−0.0258218%。一定1000USDC想定元本で7日約$1.808。rolling24hの率合計は−0.05490151%〜0%。瞬間値を年率予測に延ばさない。
- 厳密なfunding金額は各決済時のoracle notional×rateなので、率の単純和による額は近似。

## ヘッジ数量と残存リスク
円を評価通貨とすると、LPの為替deltaは概ねその時点のUSDC在庫数量。JPYC在庫まで含むTVL全額を機械的にショートしない。S=JPY/USD、U=USD在庫とし、指数がSでUSDC決済の線形契約をショートn unitsなら、entry近傍の円換算deltaを消す目安は n≈U/S。これは約U USDCの表示notionalに相当する。例: U=1000、S=158なら約6.33 unitsであり1000 unitsではない。

これは局所近似で、USDC担保、手数料、未実現損益の為替deltaも必要に応じ含める。LP在庫は変動し、ヘッジ数量は固定できない。円高ではLPのUSDC円価値減少にショート益が対応し、円安ではその逆。ただし清算・必要証拠金を無視すると、総資産が経済的にヘッジされていても先物側だけ清算され得る。

FXヘッジで消えないもの: JPYC自体のデペグ/償還停止、USDC信用リスク、LPの負のgamma・LVR、価格乖離とオラクルbasis、週末の価格発見、取引所/ブリッジ/スマートコントラクトリスク。

## 費用と比較の注意
LP純利益は、手数料−LP価格変動損失−再配置gas−ヘッジ売買費用−funding−その他損失。LP比率はTVL比ではなく、各時刻のアクティブliquidity比。
Polygonで観測した総swap fee0.0625%の全額がLP収入ではない。LP料率0.05%部分とprotocol feeを分ける。今回約40万USDCの外部grossvolume×0.05%≈200USDCはプール全体の概算手数料規模で、個別LPの利益や年率ではない。
HL current APIでgrowthMode enabled、deployerFeeScale1.0を確認。公式料金式でtier0・割引なし・aligned quote効果なしならtaker0.009%/maker0.003%の参考値となるが、個人の実際のtier/割引/担保条件は未確認。注文前に実表示料率で再評価する。

## 出典と検証限界
- https://api.hyperliquid.xyz/info (`metaAndAssetCtxs`, dex=`xyz`; `l2Book`, coin=`xyz:JPY`; `fundingHistory`)
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals
- https://docs.trade.xyz/overview/about-trade-xyz

公式trade.xyzの詳細FX仕様ページへの直接取得はHTTP403のため停止。名称・指数水準・USDC契約の観測はできたが、個別oracleの構成/乗数/詳細取引時間について本文確認未了。実際のヘッジ発注前には個別仕様とPnL感応度を追加確認する。

## 保存した公開スナップショット

`jpy_market_snapshot.json` は metaAndAssetCtxs 応答から xyz:JPY のみ抽出。`jpy_book.json` は取得時点の板、`jpy_funding.json` と `jpy_funding_7d.json` は funding 履歴です。今後の価格・板・funding・料率は変わり得ます。LP＋ヘッジの実装やバックテストは含みません。
