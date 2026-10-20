# JPYC 売却直後の買い取り：バックラン設計メモ

確認日：2026-10-20 UTC。概念検討と一次資料の確認のみ。新規 Bot の実装、デプロイ、実売買、バックラン戦略の収益測定は行っていない。

## 狙いと構成

他者の JPYC 売却でプール価格が下がった後、利益の残る数量だけ買い、後日 1 JPYC = 1 円で償還する構成は技術的に検討できる。利益増加の可能性は Solidity 自体ではなく、5 分刻みの判定では見逃す一時的な価格差を捕捉できる点にある。売却前に先回りして相手の約定を悪化させるサンドイッチを前提としない。

基本構成は「オフチェーン監視 → 売却後の状態をシミュレーション → 数量別損益判定 → Solidity コントラクトで条件付き買付」。コントラクト単体はイベントを監視して自律起動しない。既存プールへの hook 後付けもできない。新規 hook pool の注文獲得・ルーティング・同一プール内取引の注意は [HOOK_ROUTING.md](HOOK_ROUTING.md) を参照。

## 実行タイミングと競争

- 確定ブロックの Swap ログを検出する方式では、その売却を含むブロックより後に買付を送る。同じブロックで直後に入れる保証はない。
- 未確定取引から売却後を予測する方式では、対象取引の可視性、競合、並び順を扱う必要がある。Public RPC だけでは直後の順序や成功を保証できない。
- Polygon FastLane にバックラン bundle の仕様は存在するが、今回、稼働状況・対応 producer の範囲・対象注文の取り込み・実際の採用率は検証していない。Ethereum 向け Flashbots の仕様を Polygon / Avalanche の可用性の根拠にしない。Avalanche の利用可能な順序制御経路も未検証。
- Polygon の private mempool 経由の注文は公開 mempool 監視から見えない場合がある。全売却を事前捕捉できる前提にはしない。

## 利益判定とリスク制御

数量ごとに、その時点の流動性・tick・価格 impact・DEX 手数料を反映した受取額を計算する。CoinGecko の集計価格だけで判断しない。見込償還額から取得費、ガス、採用のための bid、失敗費用、必要なら資金拘束費用を差し引く。利益が正の範囲でも、最大利益数量と最大購入可能数量は区別する。

コントラクトには最大投入量、最低受取量、期限、許可トークン／プール、残高条件などを設ける。USDC 建て支出を円換算する場合、USD/JPY 参照値の信頼性と鮮度、USDC の円換金条件も必要。これらの条件は将来の償還や為替を保証するものではない。失敗して revert しても、一般にガスまで無料にはならない。

## 1 円償還とフラッシュローンは別問題

JPYC 購入後の銀行口座への円償還は、買付と同じ EVM トランザクション内で完了する出口ではない。「保有 JPYC × 1 円 − 取得費」は償還前の評価であり、その時点の現金利益ではない。実際の資金回収には償還条件・時間・再投入の制約が残る。

通常の無担保フラッシュローンは同一トランザクションで元本と手数料を返す必要があるため、この後日償還だけを返済原資にはできない。一方、同一チェーンの複数 DEX を回って借入資産を増やして戻す原子的裁定は別戦略である。担保付き借入への転換が可能な仕組みも、無担保で無料の資金調達を意味しない。

## 次に比較すべき Paper 条件

既存の 5 分判定と、JPYC 売却イベント後の判定を同じ期間・資本・費用条件で比較する。即時状態、1 ブロック遅延、競合に先を越される場合を分け、自分の買付 impact を後続状態に残す。売却イベント数をそのまま利益機会数にしない。

現時点では追加利益は未測定。速度改善で拾える差額より、競合・bid・ガス・失敗・資金拘束の負担が大きければ、利益は増えない。

## 一次資料

- EVM スマートコントラクトの基本：https://ethereum.org/developers/docs/smart-contracts/
- Uniswap v4 hooks と PoolKey の不変性：https://developers.uniswap.org/docs/protocols/v4/concepts/hooks
- Polygon FastLane bundle 仕様：https://fastlane-labs.gitbook.io/polygon-fastlane/searcher-guides/bundles-backruns/bundle-format
- Polygon private mempool 公式説明（2026-04-02）：https://www.polygon.technology/blog/polygon-launches-private-mempool-mev-protection-is-now-a-one-line-integration
- Aave flash loan の返済条件：https://www.aave.com/docs/aave-v3/guides/flash-loans
