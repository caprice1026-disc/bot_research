# 更新: stateful 連続評価が追加されました

このディレクトリは独立した過去 quote スキャンです。自己影響保持・有限資本の最新シミュレーションは ../stateful/README.md と ../stateful/RESULTS_JA.md を参照してください。以下の旧スキャン自体では連続状態を再現しません。

# JPYC Paper 検証

## 現時点の結果
過去 285 点 × 4 サイズの実測 Quoter 見積りを取得し、条件付き機会スキャンを実行しました。結論は RESULTS_JA.md、CSV は measured_results/ にあります。累積実現損益は未算出であり、0 円ではありません。

単体テストは架空の数値で計算ロジックを確認するもので、戦略の有効性や過去利益の検証ではありません。

## 同梱データで再実行
このフォルダで以下を実行します（Python 標準ライブラリのみ）。

python3 evaluate_measured.py ../public_rpc/grid/quotes_raw.json ../reference/seed_aligned_reference_prices.csv measured_results

python3 evaluate_first_snapshot.py ../public_rpc/grid/quotes_raw.json ../public_rpc/fine_first_decoded.json ../reference/seed_aligned_reference_prices.csv measured_results/first_snapshot_size_sensitivity.csv

公開データ取得元： https://tenderly.rpc.polygon.community （Polygon の履歴 eth_call）。為替参照：CoinGecko usd-coin / polygon-ecosystem-token の JPY market_chart。../public_rpc/grid/validation.json は全 285 時点の履歴ブロック・数量別 quote の検証結果です。再実行コードには送信・取引機能も秘密鍵もありません。

## 実装の範囲
paper_scan.py は USDC → JPYC の片方向見積りを評価する、送金機能のないツールです。逆方向やプール間の往復裁定は未実装です。

- 調達・送金コストは除外。数量別見積りに含まれる LP 手数料と price impact は二重控除しません。
- swap のガス代を差し引き、1 JPYC = 1 円の償還基準価値と比較します。償還の実行・入金を意味しません。
- 事前に固定した注文サイズ候補の中から、現在の見積りだけで利益が最大かつ厳密に正の数量を選びます。将来価格からの最適化はしません。
- 小分けしても impact は消えません。同一の連続機会では最初の観測だけを条件付き候補とし、投入資本は再利用しません。
- これは自己売買後のプール状態を再現するバックテストではありません。独立した過去見積りの利益を足して運用損益にしません。

## 必要な入力
1 件につき timestamp（時差付き ISO 形式）、block_number、pool_id、amount_in_usdc、amount_out_jpyc、fx_jpy_per_usdc、gas_cost_jpy、source_kind、source が必要です。数量はトークン単位、金額は円。source_kind は historical_pool_quote、source は検証可能な取得元を記録します。

gas_cost_jpy は全 swap トランザクションの推定ガス代です。Quoter 単体の計算ガスだけを流用してはいけません。FX は意思決定時点以前に取得可能だった価格を使います。取得済みの集計価格には公表遅延が未確認という限界があります。

empty_quotes.json は空の入力例です。実際の取引データに見せかけた架空の見積りは同梱していません。

例: python3 paper_scan.py quotes.json --capital-usdc 100 --output-prefix scan

100 USDC はコマンド例であり、ユーザーの資本や採用済み戦略の仮定ではありません。実行前に資本上限と数量候補を固定してください。ガス感応度は --gas-multiplier 0 / 1 / 2、受取額の悪化感応度は --execution-haircut-bps で指定できます。

テスト: python3 -m unittest -v test_paper_scan.py

完全な状態依存バックテストには、履歴ブロックのプール状態、初期化済み tick と流動性、外部 swap・流動性変更のイベント、実行遅延・ガスのモデルが必要です。詳細は METHODOLOGY.md を参照してください。
