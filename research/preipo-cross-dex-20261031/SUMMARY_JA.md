# 精査結果

基準日：2026-10-31。時刻は特記がなければ UTC。以下の数値は研究担当の精査済み結果であり、このPCでは元データの取得失敗により未再現。

## 結論

**OpenAI の Aster USDT 買い / Lighter 売りには単位換算後も鞘が残る。しかし、収束・Funding・出口流動性・IPO 処理に依存する相対価値取引であり、即時に利益確定できる裁定とはいえない。**

Lighter の総株数10億株仮定と Aster の希薄化後参照株数10億株仮定は表示スケールを揃えるが、株式クラスや最終契約価値の一致を保証しない。USDT と USD/USDC の等価も仮定している。出典：[Lighter 仕様](https://docs.lighter.xyz/trading/real-world-assets-rwas/market-specifications)、[Aster 仕様](https://docs.asterdex.com/trading/perpetuals/pre-ipo-perpetuals)。

## 持続する価格差と原因の候補

OpenAI の Lighter プレミアムが2%以上となる状態は、**2026-09-09 11:00〜2026-10-31 05:00 の1時間足1,243標本で連続**した。両側に約定がある366時間だけでも中央値は **+5.664%**。検証対象のゼロ出来高時間比率は Aster **70.4%**、Lighter **0.7%**だった。

流動性の非対称は確認できるが、それが価格差を生んだ因果関係までは証明していない。9月9日前後の株数・単位変更を裏づける一次資料も見つかっていない。研究担当が確認した Lighter の `priceOracleInfo` では、両銘柄の Index/Mark が `internal_price`、`median_sources` は空。Aster 価格への強制的な収束構造は確認できない。[価格設定仕様](https://docs.lighter.xyz/trading/real-world-assets-rwas/pre-ipo-markets)、[公開API](https://mainnet.zklighter.elliot.ai/api/v1/priceOracleInfo)。

Lighter の7月20日08:00の Pre-IPO Funding premium 99%縮小、9月13日11:00の計算式変更も検討した。9月13日の変更は9月9日の乖離開始より後なので、その開始原因の説明にはならない。[公式告知API](https://mainnet.zklighter.elliot.ai/api/v1/announcement)、[API更新告知](https://t.me/lighter_api_updates/173)。因果についての結論は保留。

## 板から見た入口と即時の出口

2026-10-31 **06:35:59〜06:36:59**に並列 GET で3標本を取得した。応答窓は順に **11.17 / 3.90 / 4.74秒**であり、原子的に同時の板ではない。共通数量を0.01単位で切り下げ、予算・最低数量・最低額・板深度を検証したが、注文受付は試していない。

OpenAI の入口の価格差は **+4.32〜+4.35%**。それでも入口2脚の直後に出口2脚を反対売買すると、基本手数料込みの結果は次のとおりだった。

| Aster 買い予算 | OpenAI の即時4脚反対売買 |
| --- | ---: |
| 100ドル | −0.67〜−0.69% |
| 1,000ドル | −0.78〜−0.80% |
| 5,000ドル | −1.02〜−1.03% |

Anthropic の1,000ドル入口差は、3標本で **+0.956% → −2.075% → +0.984%** と変わった。一時点の表示差だけで執行可能性を判断できない。

Aster RWA taker **1.25bps**、Lighter metadata の `taker_fee=0` を基本ケースに使用。実口座の適用手数料は未確認。Lighter 3bps は任意の感応度であり、Premium口座の実料金とは扱わない。[Aster 手数料](https://docs.asterdex.com/program-and-rewards/vip-program.md)。

## 鞘が変わると仮定した出口シナリオ

最終標本について、両市場の中値平均を固定し、現在の方向別VWAPと中値の差を出口でも維持した。基本手数料を含み、**Funding は除外**。以下は価格予測ではなく、指定した仮定の損益感応度である。

| 市場・Aster買い予算 | 鞘不変 | 半減 | 全縮小 |
| --- | ---: | ---: | ---: |
| OpenAI 1,000ドル | −$7.76 | +$15.49 | +$38.74 |
| OpenAI 5,000ドル | −$51.05 | +$65.50 | +$182.06 |
| Anthropic 1,000ドル | −$12.37 | −$4.75 | +$2.87 |
| Anthropic 5,000ドル | −$226.72 | −$189.52 | −$152.32 |

OpenAI には収束した場合に利益が残る仮定がある一方、Anthropic 5,000ドルでは全縮小でも仮定した往復費用を回収できなかった。将来の板・手数料・片脚の約定は保証されない。

## Funding と歴史シナリオ

条件付きの `rate/direction` 解釈による一定等額建玉の30日率合計は、Aster買い / Lighter売りで **OpenAI −0.1495%、Anthropic −0.4393%**。これは固定数量の実現キャッシュ損益でも、検証済み carry でもない。Lighter は Index、Aster は Mark を使い、Lighter履歴の `value/direction/timestamp` に未回答点、Aster履歴に精算Markの不足がある。[Lighter Funding](https://docs.lighter.xyz/trading/funding)、[Aster Funding](https://docs.asterdex.com/trading/perpetuals/fees-and-specs/funding-rate)、[未解決の定義確認](https://github.com/elliottech/lighter-python/issues/182)。

入口のプレミアム2%以上、Aster買い1,000ドル基準の同数量を7日保有した歴史上の **Mark PnL** は、中央値 **+$3.47**、下位10% **−$19.50**。Funding・手数料・板を除外し、観測窓も重複する。執行バックテストや将来勝率として解釈しない。

## 次に必要な証拠

最終的な株式クラス・希薄化・換算倍率、IPO時の数量/取得価調整と先行精算条件、Funding履歴定義、実口座の手数料・注文制限、片脚未約定・清算・ADLを確認する必要がある。具体的な未確認点は [LIMITATIONS.md](LIMITATIONS.md) にまとめた。
