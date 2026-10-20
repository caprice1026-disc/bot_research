# JPYC 裁定研究：公開 RPC による Paper 検証

Polygon の現行 JPYC / USDC Uniswap v4 プールを使った、読み取り専用の調査・オフラインシミュレーションです。実取引、署名、秘密鍵、償還処理はありません。

**最新結果は [1 円償還・再投入の比較](stateful/REDEMPTION_RESULTS_JA.md)。1 JPYC を必ず 1 円で償還し続ける前提で、即時 / 1 時間 / 24 時間の資金循環と注文額を比較しています。実サービスの処理時間・制限を保証するものではありません。**

初期の [資金再利用なしの連続評価](stateful/RESULTS_JA.md) は対照実験です。同じ DEX へ売り戻す出口と 1 円償還の出口は異なり、DEX 往復が赤字でも償還の差益を否定するものではありません。

## 収録範囲

- 固定検証期間：2026-10-19 11:35:00 ～ 2026-10-20 11:13:20 UTC（JST 10月19日20:35 ～ 20日20:13:20）。現在からの最新24時間ではありません。
- 285 時点 × 4 サイズ（1 / 10 / 100 / 1,000 USDC）の過去 Quoter 見積もり。追加で最初の時点の少額サイズを検証。
- 初期状態：ブロック 95802902。以降 95859621 まで全対象プールイベント 149 件（Swap 120、ModifyLiquidity 29）。初期 tick は限定範囲の108個。
- USDC/JPY と POL/JPY は CoinGecko 集計値。実際の換金価格ではなく、公表遅延も未検証。
- 参照利益、ガス感度、有限資本、自己 swap の price impact を保持した条件付き再生。有限の円元本・償還待ちを管理し、即時償還でも各観測時点の注文は1回までです。

## 利益の源泉

[実ログ・条件除去実験による考察](profit_sources/README_JA.md) では、JPYC 売り超過、初期の割安在庫、FX・LP 変更の影響を分けて検討します。オンランプ需要という仮説と、ログから確認できた事実は区別しています。

## ディレクトリ

- `profit_sources/`: 売買フロー集計・条件除去実験・利益源泉の考察。
- `stateful/`: 最新の自己影響保持型シミュレーター、テスト、CSV、結果。
- `analysis/`: 各時点を独立評価する機会スキャン。ここの候補利益を合算して運用利益にはしません。
- `public_rpc/`: 過去見積り取得・検証コードと固定取得データ。
- `replay_data/`: 初期 tick、全プールログ、イベント検証、読み取り専用の収集コード。
- `reference/`: 円換算用の固定参照価格と出典。
- `rpc/`: 認証不要の読み取り RPC リクエスト・pool ID 計算。
- `manifest.json`: 公開ファイルの SHA-256。入力データ・取得時点は固定し、新しい窓は別版として区別します。

## オフライン再実行

Python 3.10 以上、標準ライブラリのみ。リポジトリルートから次を実行します。

```sh
cd jpyc-arbitrage-research
python -m unittest discover -s analysis -p 'test*.py'
python -m unittest discover -s stateful -p 'test*.py'
python public_rpc/validate_grid.py
python replay_data/finish_dataset.py
python stateful/run_stateful.py
python stateful/run_redemption.py
python analysis/evaluate_measured.py public_rpc/grid/quotes_raw.json reference/seed_aligned_reference_prices.csv analysis/measured_results
python analysis/evaluate_first_snapshot.py public_rpc/grid/quotes_raw.json public_rpc/fine_first_decoded.json reference/seed_aligned_reference_prices.csv analysis/measured_results/first_snapshot_size_sensitivity.csv
```

保存済みデータを使う上記の評価・テストに送金機能はありません。`finish_dataset.py` は同梱ヘッダが足りる場合はオフラインですが、入力を変更してヘッダが不足すれば公開 RPC 読み取りを行います。再実行は結果ファイルを上書きするため、比較したい場合は先に作業コピーを作ってください。

`public_rpc/collect_grid.py` と `replay_data/collect_replay.py` は明示的に実行した場合のみネットワークにアクセスし、固定期間の未取得部分を取得します。既存入力の削除は不要です。RPC の公開枠・履歴アクセスは将来保証されず、認証要求やアクセス拒否を迂回しません。実取引 RPC は使いません。

## 費用・解釈

調達・送金コストは除外。実 quote と再生 math は LP + protocol fee（この期間は合成 0.0625%）とサイズ別 impact を反映済みで、二重控除しません。gas は全 router 実測ではなく明示した基準/高コスト仮定です。承認・失敗取引・MEV・執行遅延は再現していません。

他の参加者の注文入力・流動性変更を固定して再生する反実仮想です。実際には競合や LP が反応するため、この仮定が実現する保証はありません。元の exact-output 注文・ルート・価格制限はログだけからは復元できません。基準履歴の整合検証と将来の収益性は別です。

## 保存方針と出典

公開チェーンデータと研究成果だけを保存。認証情報、HTTP ヘッダ、環境固有ログ、ウォレット情報、キャッシュ、生成 ZIP は含めません。ブロック JSON は number/hash/parentHash/timestamp/baseFeePerGas の必要フィールドへ正規化し、不要な取引 hash 配列等は省略。詳細な独立見積り CSV と全80償還シナリオの台帳はコードで再生成できます。償還版は全サマリーと代表3条件の購入・受取台帳を収録しています。

- [Polygon RPC 一覧](https://docs.polygon.technology/pos/reference/rpc-endpoints)
- [Uniswap v4 配備先](https://developers.uniswap.org/docs/protocols/v4/deployments)
- [Uniswap v4 core](https://github.com/Uniswap/v4-core) / [StateView](https://github.com/Uniswap/v4-periphery/blob/main/src/lens/StateView.sol)
- [CoinGecko jpycoin](https://www.coingecko.com/en/coins/jpycoin)

旧前払式 `jpy-coin` と現行 `jpycoin` は混同しません。公開RPCと集計価格の稼働状況はこの取得時点の結果であり、常時利用可能とは保証しません。
