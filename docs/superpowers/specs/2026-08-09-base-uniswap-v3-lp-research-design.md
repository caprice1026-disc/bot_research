# Base Uniswap v3 LP研究 v0.1 設計書

## 目的

この研究では、Baseの低い取引コストが「狭い価格レンジを高頻度に再配置する」Uniswap v3 LP戦略を成立させるかを、実データで判定する。実装後は、Base WETH/USDC 0.05%プールのSwap等を順番どおりに再生し、HODL、Full Range、Static Narrow、τ-reset等を同じ条件で比較できる。最初の結論は、利益率の最大値ではなく、HODLに対するネット超過収益が、再配置のswap friction、BaseのL2実行費、L1 security fee、LVR、遅延仮定を含めても、未観測期間で再現するかで表す。

## 承認済みスコープ

今回の第一完成単位は、仕様書のv0.1に合わせてAI/RLを使わないC0反実仮想バックテスターとする。C0とは、検証対象LPが過去のプール価格経路を変えないという小資本近似であり、後段のC1/C2で感度を調べる。初期実験にはBase WETH/USDC 0.05%を使い、可能な範囲で同期間のEthereum比較用データを追加する。データ期間は設定で指定できるようにし、まず3か月pilotを取得し、9か月以上のpoint-in-timeデータが揃った場合だけwalk-forward/OOTを実行する。期間不足をゼロ損益の成功結果へ置き換えない。

対象は、プール解決、Swap/Mint/Burn/Collectログ、ブロック時刻、取引receipt、tick/liquidity再構築、P0 fee近似、Base費用モデル、ベースライン戦略、資本・レンジ・トリガーのsweep、指標、検証レポートである。ML、RL、AI Agent、ウォレット署名、実資金取引、v3の完全なSwapMath再現は今回の範囲外とする。

## 採用したアプローチ

第一案は、ノートブックだけで集計する方法である。短時間でグラフを作れるが、イベント順序、将来情報混入、費用の根拠、再実行性を検証しにくいため採用しない。第二案は、RPC取得・正規化・純粋なバックテスター・CLI・テストを一つの研究ディレクトリに収める方法であり、今回これを採用する。第三案は最初からML/RL/Agentまで一括実装する方法だが、基礎会計とデータ品質が未検証の状態で過学習と説明不能性を持ち込むため採用しない。

## データと再現性

研究ディレクトリは `base-lp-research/` とし、生成データはGit管理外の `data/` に保存する。`BASE_RPC_URL`を第一取得経路とし、RPC制限時には公式が案内するindexerをadapterとして追加できるようにする。プールアドレスはコードへ固定せず、Uniswap v3 Factoryの `getPool(tokenA, tokenB, fee)` を実行して解決し、chain ID、token順、decimals、fee tier、creation blockをmanifestへ保存する。Uniswapの公式deployments情報はアドレス確認のsource of truthとする。

イベントは `(block_number, transaction_index, log_index)` を安定キーとして厳密に並べる。同一ブロック内のSwapをまとめない。各データセットには、取得元、block range、取得時刻、schema version、レコード数、SHA-256 checksumを記録する。RPCの欠損、重複、block hash不一致、topic不一致、token metadata不一致はエラーとして記録し、分析結果を生成しない。

Parquetを本番保存形式とし、PyArrowが利用できない場合はテスト用JSONLを使えるが、実データの研究結果をCSVだけで完了扱いにしない。外部依存の導入に失敗した場合は、取得できた範囲と失敗理由をmanifestへ残し、`insufficient_data`を返す。

## バックテスター

`Swap`イベントのamount、post-swapのsqrtPriceX96、tick、liquidityをchain orderで処理する。v0.1では、対象LPがレンジ内にいるときのinput amount、fee tier、対象LPのactive liquidity shareからfeeを近似する。tick crossingを完全なsegmentごとに再現するP1、Uniswap v3-coreのSwapMathとtraceを用いるP2は、v0.1の検証結果を見て追加する。

ポートフォリオ価値は、同一評価時点のtoken0/token1残高をUSDC換算し、未回収feeを加え、再配置費用を引く。HODL比較は初期token配分を保有し続けた価値との差分とし、LVRはHODL比較と混同しない診断系列として別出力する。strategyの判断は当該Swap以前のイベントだけを参照し、future event、future volatility、期間終値を参照したらテストで失敗させる。

## 戦略と費用

最低限、HODL、Full Range、Static Narrow、τ-resetを実装する。Static Narrowはレンジ外でも保持し、τ-resetはレンジ外で現在価格中心へ再配置する。実験本命のThreshold Resetは、中心からedgeまでの距離に対する比率 `q`、cooldown、execution latencyを持ち、expected benefitが推定費用と安全余裕を超えない限り「何もしない」。

費用は `L2 execution fee + L1 security fee + inventory swap fee + price impact + slippage/MEV proxy`へ分解する。historical receiptにL1 fee情報がある場合はそれを優先し、ない場合は明示的な推定値として扱い、p25/p50/p75シナリオを分ける。初期gasモデルの仮定だけで戦略優位性が消えないかを必ず報告する。

## 評価と受入条件

主KPIは、walk-forward/OOTのHODL-relative net alphaである。Max Drawdown、CVaR、fold win rate、Time In Range、gross fee、gas/fee、swap cost/fee、rebalances/dayを併記する。Gross fee APRだけで戦略を採用しない。

受入条件は、(1) unit/golden testでtickとliquidity計算が固定される、(2) fixture replayが同じchecksumと同じ結果を再現する、(3) selected blockのon-chain state照合が通る、(4) no-lookahead testが通る、(5) 実データpilotが欠損なしまたは明確な `insufficient_data` で終了する、(6) 結果manifestにconfig、commit、block range、dataset checksum、source、仮定が残る、の6点とする。narrow + fastの収益性は実データを走らせるまで未確定とし、結果がない状態で肯定しない。

## 研究ディレクトリの境界

`base-lp-research/src/base_lp/data/`は取得・イベント・保存、`uniswap_v3/`は整数tick/liquidity計算、`backtest/`は状態再生・ポートフォリオ・費用、`strategies/`は純粋な再配置ルール、`metrics/`は評価、`experiments/`はsweepとreportを担当する。CLIは`src/base_lp/cli.py`に集約し、データ取得と分析を別コマンドにする。テストは`tests/unit/`、`tests/golden/`、`tests/integration/`へ分ける。

## 決定ログ

- 決定: 最初の完成単位をAIなしC0 v0.1とする。理由: 数理・データ品質・会計を先に検証し、ML/RLの過学習を避けるため。日付: 2026-08-09。
- 決定: 実データ不足をゼロ損益へ置換せず `insufficient_data` とする。理由: 収益性の不存在と取得不能を混同しないため。日付: 2026-08-09。
- 決定: pool addressはFactory.getPoolで実行時解決する。理由: 公式deployments更新やtoken順の誤固定を避けるため。日付: 2026-08-09。
- 決定: Base費用をL2とL1へ分解し、receiptの履歴値を優先する。理由: Baseのネットワーク費が二部構成で、L2 gasだけでは再配置費を過小評価するため。日付: 2026-08-09。
