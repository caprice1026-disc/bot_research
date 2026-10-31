# 出典と検証範囲

基準日：2026-10-31。リンク先は更新され得る。本Markdownは研究時点の確認結果を要約するもので、現在のページが過去の状態を完全に保存するとは限らない。

## 契約・制度の一次資料

| 資料 | 対応する主張・用途 |
| --- | --- |
| [Lighter Market Specifications](https://docs.lighter.xyz/trading/real-world-assets-rwas/market-specifications) | OpenAI / Anthropicの総株数10億株仮定 |
| [Lighter Pre-IPO Markets](https://docs.lighter.xyz/trading/real-world-assets-rwas/pre-ipo-markets) | 内部価格機構、Funding倍率、通常市場への移行、先行精算 |
| [Lighter公式announcement API](https://mainnet.zklighter.elliot.ai/api/v1/announcement) | 2026-06-09 SPACEX事例、2026-07-20 Funding変更の研究担当確認 |
| [Lighter priceOracleInfo API](https://mainnet.zklighter.elliot.ai/api/v1/priceOracleInfo) | internal_price、median_sources等の研究時点の確認 |
| [Aster Pre-IPO Perpetuals](https://docs.asterdex.com/trading/perpetuals/pre-ipo-perpetuals) | 希薄化後10億株、Q4 2026、市場注文価格制限、株式所有権との区別 |
| [Aster VIP Program](https://docs.asterdex.com/program-and-rewards/vip-program.md) | 2026-09-07以降のRWA taker 1.25bps |
| [Lighter Funding](https://docs.lighter.xyz/trading/funding) | Index基準の支払い、符号、現行計算方法 |
| [Aster Funding Rate](https://docs.asterdex.com/trading/perpetuals/fees-and-specs/funding-rate) | Mark基準の支払い、Fundingの仕組み |
| [Lighter API updates / 173](https://t.me/lighter_api_updates/173) | 2026-09-13の式変更に関する研究担当確認 |
| [lighter-python Issue #182](https://github.com/elliottech/lighter-python/issues/182) | 履歴valueの単位、timestamp、区間境界等の未回答点 |
| [XYZ IPOps Specification Index](https://docs.trade.xyz/consolidated-resources/specification-index-ipops) | 前段の実株1株型・クラス別比較 |
| [Anthropic S-1](https://www.sec.gov/Archives/edgar/data/2130357/000162828026067168/anthropicpbc-sx1.htm) | 前段の条件付き希薄化株数の感応度。最終IPO株数や確定倍率とは扱わない |
| [Oura S-1/A](https://www.sec.gov/Archives/edgar/data/2133022/000119312526396051/d119865ds1a.htm) | 2026-03-31の親子会社再編、09-18の2対1分割、条件付きIPO後株数、別掲option/RSU |
| [Paragon Contract Specifications](https://docs.paragon.trade/resources/specifications) | ANTの1株参照。株式クラスやAPI表記との対応は未確定 |
| [Aster Single-Asset / Multi-Asset Mode](https://docs.asterdex.com/trading/perpetuals/single-asset-mode-and-multi-asset-mode) | USD1/USDTの担保評価率、通貨別負残高の利息 |
| [Hyperliquid Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) | HIP-3 growth mode・deployer fee share・tierによる料金条件 |
| [Hyperliquid Funding](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) | XYZ/ParagonのFunding率と精算cashを区別する基礎 |

## 計算結果の来歴

37,804行の前段価格履歴、今回のプレミアム・出来高・板・Funding・7日Mark損益の数値は、同じ研究タスクの研究担当が行った取得・計算・精査の最終要約から受領した。価格系列や板の本体はGitHubに含めない。

上の契約文書だけから、表中の価格差や損益数値が直接得られるわけではない。数値の根拠となる元データと再計算資料は提供された統合ZIPに収録されるが、このPCへの転送が失敗したため、ローカルで独立再現できていない。期待ハッシュと保存状態は [LIMITATIONS.md](LIMITATIONS.md) に記載。

追加候補の132条件（2026-10-31 12:52〜12:55 UTC）も研究担当の精査済み要約を受領した。APIの板・FX・Funding本体は未保存の `preipo_other_candidates_20261031T1258Z.zip` に含まれるとの提供情報に基づく。新ZIPの自動転送は試しておらず、このPCでの再計算・ハッシュ照合も未実施。追加保存作業ではSEC Oura、XYZ、Paragon、Aster担保モード、Hyperliquid手数料・Fundingの一次資料を再確認した。研究担当のAPI観測値を、契約文書から直接読み取れる値として扱わない。

本保存作業ではLighter市場仕様・Pre-IPO・Funding、Aster Pre-IPO・VIP・Funding、Issue #182の本文を再確認した。Telegramの告知はこの保存作業の閲覧ツールでは再取得できなかったため、日時と内容は研究担当の精査結果に依拠する。APIスナップショットと元データの大量再取得は行わなかった。

Issue #182は疑問点を列挙する資料であり、利用者の質問を公式回答として扱わない。9月9日前後の株数・単位変更、両DEXの同時同価精算、実口座の注文可否を裏づける資料は未確認。
