# 追加候補の比較（2026-10-31）

観測期間：**2026-10-31 12:52〜12:55 UTC、132条件**。以下は研究担当から受領した精査済み要約で、このPCでは元データ未保存・計算未再現。前段の06時台の観測と混ぜず、通貨・取得時刻・費用条件を区別する。

## 結論と候補の優先順位

条件付きで残るのは **Anthropic の Aster USD1 買い / Lighter 売り**。ただし、FX価格の古さと未算入費用があり、無条件の実行候補へ昇格した組合せはない。これは観測した時刻・サイズの判定であり、将来の価格差やFunding戦略が存在しないという証明ではない。

| 候補 | 観測結果 | 現時点の扱い |
| --- | --- | --- |
| Anthropic Aster USD1買い / Lighter売り | 1,000・5,000 USDT予算で入口差が残り、収束シナリオで正の損益となる条件あり | FXの鮮度・費用・注文条件を詰める条件付き候補 |
| OpenAI Aster USD1買い / Lighter売り | USDT経路より入口差が小さく、大口USD1板に価格制限の疑義 | 既存USDT経路より優先する根拠なし |
| Paragon買い / XYZ売りのAnthropic | 100ドルに微小な入口差、1,000ドル以上は費用・深度が不利 | 小額の差も利益確定とはいえず、優先度は低い |
| Oura 3市場 | 54条件すべて入口差が負 | 今回標本では入口候補なし |
| Aster内の同銘柄USDT / USD1 | 36条件すべて入口差が負 | 今回標本では入口候補なし |

## Anthropic USD1経路とFXの扱い

最終標本は **12:55:33〜12:55:38 UTC**。独立に取得したFXは USD1/USDT の bid/ask **0.9989 / 0.9990**、USDC/USDT は **1.0010 / 1.0013**。数量を0.01単位で切り下げ、Aster taker 1.25bps、Lighter base taker 0 を仮定した。いずれも注文を送った実績ではなく、板に基づく計算値。

| Aster買い予算（USDT） | 計算上の買い建玉額（USDT換算） | 入口差 | 即時往復PnL | 半収束PnL | 全収束PnL |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 989.07 | +0.980% | −1.56 USDT | +3.73 USDT | +9.03 USDT |
| 5,000 | 4,998.30 | +0.911% | −16.26 USDT | +10.49 USDT | +37.23 USDT |

中値平均、FX、方向別VWAPと中値の差を固定した条件計算であり、予測ではない。**Funding・現物交換手数料・FXヘッジ・借入費用を除外**している。

各perp脚の元通貨PnLを、正なら当該FXのbid、負ならaskでUSDT評価し、合算する。建玉全額を現物交換する計算ではない。FXイベント時刻はUSD1で約975〜1,000秒、USDCで約794秒古く、**同時刻に実行できる為替価格とは保証できない**。同時取得のAnthropic USDT経路は入口差が負だった。

Aster手数料の文書は確認したが、実口座の条件は未確認。[Aster VIP Program](https://docs.asterdex.com/program-and-rewards/vip-program.md)。契約単位とIPO処理の未解決点はUSD1経路にも残る。[Aster契約](https://docs.asterdex.com/trading/perpetuals/pre-ipo-perpetuals)、[Lighter仕様](https://docs.lighter.xyz/trading/real-world-assets-rwas/market-specifications)。

## OpenAI USD1経路

| 買い予算 | USD1経路の入口差 | 同時USDT経路の入口差 |
| --- | ---: | ---: |
| 1,000 | +4.170% | +4.329% |
| 5,000 | +1.527% | +4.298% |

USD1板の末端ask **1,558.05** は直前Mark約 **1,358** から離れており、動的価格制限に抵触する疑義がある。価格制限の基準・実注文受付は未確認なので、返却された深度をそのまま執行可能な板とは扱わない。Asterの市場注文価格制限はDEX間の価格差上限ではない。[Aster仕様](https://docs.asterdex.com/trading/perpetuals/pre-ipo-perpetuals)。

## XYZ / ParagonのAnthropic

Paragon買い / XYZ売りを3標本で比較し、各組のserver timestampは一致した。時刻表示の一致だけで実注文の同時約定が保証されるわけではない。

| 片側予算 | 入口差 | 手数料込み4脚往復 | 全収束・現在VWAP摩擦維持のPnL |
| --- | ---: | ---: | ---: |
| 100ドル | +0.146〜+0.222% | −0.327〜−0.498% | −$0.120〜+$0.042 |
| 1,000ドル | −0.214〜−0.142% | −1.212〜−1.148% | −$8.492〜−$7.803 |
| 5,000ドル | 返却深度不足 | 評価対象外 | 評価対象外 |

5,000ドルは **XYZ APIが返した上位20段**の深度が不足した。市場全体で約定不能と証明したものではない。全収束シナリオにもFundingを含めていない。

growth modeとfee scaleを確認したうえで、tier 0 taker **0.009% / 脚**を仮定した。実口座の料金は未確認。[Hyperliquid Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)。

24回分のFunding率合計はXYZ **+0.00119836%**、Paragon **+0.01271841%**。Paragon買い / XYZ売りの一定等額建玉の率差は **−0.01152005%**。固定数量の実現cash損益ではなく、精算時notionalの違いを無視してゼロコストとは扱わない。[Fundingの仕組み](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)。

XYZはClass A株1株を参照する一方、Paragonの株式クラスは未記載。Paragon公式表記 `ANT` とAPI `para:ANTH` の相違、IPO時の調整・同時精算も未確定である。[XYZ仕様](https://docs.trade.xyz/consolidated-resources/specification-index-ipops)、[Paragon仕様](https://docs.paragon.trade/resources/specifications)。

## Oura：入口差と株式単位

3市場の全3組 × 両方向 × 3サイズ × 3標本の **54条件すべてで入口差が負**。最良でも Aster買い / Lighter売り、100ドルで **−0.01297%** だった。

SEC資料ではOura Inc.が親会社、旧Oura Health Oyが子会社となる再編と、旧保有者の経済的利益・株主権の維持が記されている。2026-09-18に2対1株式分割が行われ、対象の株数・1株情報は分割を反映している。[Oura S-1/A](https://www.sec.gov/Archives/edgar/data/2133022/000119312526396051/d119865ds1a.htm)。

Asterの参照株数 **320,945,459株** は、SEC資料の条件付きIPO後発行済普通株数と一致する。ただし、別掲のoption・RSUもあるため、真の完全希薄化後株数と同一視しない。Lighterの旧法人表記が「分割後の親会社1株」に対応するという公式明示は確認できておらず、独自判断で1対2の換算を加えない。[SEC資料](https://www.sec.gov/Archives/edgar/data/2133022/000119312526396051/d119865ds1a.htm)、[Aster仕様](https://docs.asterdex.com/trading/perpetuals/pre-ipo-perpetuals)、[Lighter仕様](https://docs.lighter.xyz/trading/real-world-assets-rwas/market-specifications)。

## Aster内のUSDT / USD1

OpenAI・Anthropicの同銘柄を両方向・3サイズ・3標本で比較した **36条件すべてで入口差が負**。最良値はOpenAI **−0.719%**、Anthropic **−0.790%** だった。

直近3回のFundingでは、4銘柄が同時刻・8時間間隔で **+0.005%**。率差による収入は観測できなかったが、建玉評価額が違い得るため実cashが必ずゼロとは断定しない。

Multi-Asset Modeの文書では、対象チェーンのUSD1担保評価率は **99%**、USDTは **99.99%**。各通貨を独立に判定し、負残高が1,000 USD相当を超える部分には時間利息がかかり、観測時の年率は **4%**。口座モード・残高が不明なので、すべての取引へ固定費として加算していない。担保評価率も往復手数料ではない。[Aster担保モード・利息](https://docs.asterdex.com/trading/perpetuals/single-asset-mode-and-multi-asset-mode)。

## 残る検証と元データ

条件付き候補の次の確認点は、鮮度の揃ったFX、元通貨PnLと実際の資金調達、Funding、実口座fee、動的価格制限、IPO時の同一性・数量/取得価調整である。価格フィットや平均差の除去で鞘を消す換算は行っていない。

元データ `preipo_other_candidates_20261031T1258Z.zip` は **292,207 bytes**、提供元の期待SHA256は `7ed5d368d212c291336ff791938dff1fab874f4420b87c31e9921a37851e0b30`。手動取得用として提供されたが、このPCには未保存で、ハッシュも未照合。既知のWindows属性保存問題に対する再転送は行わず、GitHubには要約だけを掲載する。[保存状態](LIMITATIONS.md)、[出典](SOURCES.md)。
