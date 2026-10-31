# 契約・価格形成・IPO処理

基準日：2026-10-31。仕様の文言と研究担当のAPI・告知確認結果を整理する。未確認事項を将来の契約保証として補わない。

## 表示スケールと権利

| 市場 | 参照単位 | 残る問題 |
| --- | --- | --- |
| Lighter OpenAI / Anthropic | 総株数10億株を仮定した1株価格 | 実際の株式クラス、最終株数、IPO時処理 |
| Aster OpenAI / Anthropic | 希薄化後参照株数10億株を仮定 | Lighterとの希薄化範囲・最終契約価値の一致 |
| XYZ ANT | Class A株1株型 | 外部株数を使った換算が必要 |
| Paragon | 1株型としての条件付き比較 | 株式クラス未確認 |
| io | 提供説明に基づく仮定 | 専用契約仕様の独立確認が未完了 |

両主対象が「10億株」という同じ数値を使うことは、同一の権利・契約を意味しない。AsterのPre-IPO契約は株式所有権を与えない。[Lighter仕様](https://docs.lighter.xyz/trading/real-world-assets-rwas/market-specifications)、[Aster仕様](https://docs.asterdex.com/trading/perpetuals/pre-ipo-perpetuals)、[XYZ仕様](https://docs.trade.xyz/consolidated-resources/specification-index-ipops)。

## 価格差を強制的に閉じる仕組みは未確認

研究担当の `priceOracleInfo` 確認では、Lighter両銘柄の Index と Mark が `internal_price`、`median_sources` が空。文書もPre-IPOは内部の価格機構によると説明している。これらから、Aster価格への強制収束を保証する仕組みは確認できない。[Lighter価格設定](https://docs.lighter.xyz/trading/real-world-assets-rwas/pre-ipo-markets)、[価格oracle API](https://mainnet.zklighter.elliot.ai/api/v1/priceOracleInfo)。

Asterの **5%** は市場注文の価格上限/下限比率であり、DEX間の価格差上限ではない。`Outside Launch Date: Q4 2026` も、両DEXが同時刻・同価格で精算する保証にはならない。[Aster市場仕様](https://docs.asterdex.com/trading/perpetuals/pre-ipo-perpetuals)。

Lighter文書のPre-IPO「価格上限なし」と、APIで研究担当が観測した Mark cap **2000bps** は、異なる制限層を指す可能性がある。仕様の対応が未確定なので「Markが完全に無制限」と結論しない。

## 制度変更の時系列

| UTC時刻 | 確認された変更・観測 | 解釈 |
| --- | --- | --- |
| 2026-07-20 08:00 | Lighter Pre-IPO Funding premiumを99%縮小 | 通常perpのFundingをそのまま当てはめない |
| 2026-09-09 11:00 | OpenAIの2%以上プレミアム連続区間が開始 | 株数・単位変更の一次証拠なし |
| 2026-09-13 11:00 | Lighter Funding式変更 | 9月9日の開始原因にはならない |

出典：[公式告知API](https://mainnet.zklighter.elliot.ai/api/v1/announcement)、[API更新告知173](https://t.me/lighter_api_updates/173)、[現行Funding文書](https://docs.lighter.xyz/trading/funding)。過去の計算に現行式を遡及適用しない。変更の存在と価格差の原因は別の主張である。

## IPO時の処理

Lighter文書は、IPO後に安定した価格フィードが利用できると通常市場へ移行し、株式分割で価格が大きく乖離する場合は先行精算する可能性を記す。[Conversion and Settlement](https://docs.lighter.xyz/trading/real-world-assets-rwas/pre-ipo-markets)。

研究担当が確認した **2026-06-09の公式SPACEX事例**では、株数判明後に別のSPCX市場を併設し、旧SPACEXは自市場Markで精算した。[公式告知API](https://mainnet.zklighter.elliot.ai/api/v1/announcement)。これはOpenAIにも同じ処理を行うという予告ではない。自動的に両DEXが同じ株価へ機械換算されるという保証も読み取れない。

線形損益 `q(P − E)` を保ったまま表示価格を `P' = P/k` にするには、例えば `q' = kq`、`E' = E/k` など対応する数量・取得価調整が必要。表示価格の除算だけでは損益は保存されない。実際に各DEXが採用する数量、取得価、建玉、証拠金、先行精算価格の処理は未確認。

## Fundingの基準と未回答点

LighterのFunding支払いはIndexを基準にし、AsterはMarkを基準にする。[Lighter](https://docs.lighter.xyz/trading/funding)、[Aster](https://docs.asterdex.com/trading/perpetuals/fees-and-specs/funding-rate)。

Lighter履歴の `value/direction/timestamp` の厳密な意味、区間境界などは [lighter-python Issue #182](https://github.com/elliottech/lighter-python/issues/182) にも確認事項が残る。Issueは質問の存在を示す資料であり、未回答の解釈を公式仕様として扱わない。Aster履歴の精算Mark不足と併せ、実現Fundingキャッシュ損益の確定を妨げる。
