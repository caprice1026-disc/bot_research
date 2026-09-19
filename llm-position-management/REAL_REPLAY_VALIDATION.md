# 実1分足・Fundingによるscripted Replay

2026-09-19実施。目的は新方式の建玉管理・費用・永続化の機能確認であり、売買戦略の採用判定ではない。対象は保存済みBinance USD-M BTCUSDT、UTCの2025-09-01一日、その後2025-09-01から2025-10-01未満の一か月。実行コマンドはREADMEに記載した。

## 入力と固定条件

ローソク足の内容hashは既存manifestと一致した。SHA-256は `1832caee2dc69ff72f8545e0588f3e9de26da811478bd8ecc535f81b8e301dc6`。Fundingの9月90件は元ZIPと時刻・間隔・rateが一致し、元ZIPは配布CHECKSUM `21dc6570fc6bffad0e7b5f9e99211b82ad07c95ab4f18c3a2daa3b95ec98344c` と一致した。

初期資産1,000 USDT、固定anchor 250 USD、片道fee 0.045%、spread 2 bps、slippage 1 bp、初期SL 2%。6時間周期で方向を交互に切り替え、5分ごとの判断slotで新規・維持・追加・縮小・決済を行う固定scriptを使用した。判定は現在の観測だけを参照し、結果に応じた閾値調整は行っていない。

入力の不足・重複・逆順・異なる市場・未来の利用可能時刻は拒否する。毎tickの状態をSQLite WALへ保存し、途中で終了して境界tickを重複入力した再開結果と、連続実行の全判断・全イベント・費用・最終口座を比較する。全イベントと実行設定、入力hashはローカルの各成果物ディレクトリへ保存する。

## 検証結果

| 項目 | 2025-09-01 | 2025-09全体 |
| --- | ---: | ---: |
| 1分足 | 1,440 | 43,200 |
| Funding入力件数 | 3 | 90 |
| 判断件数 | 288 | 8,640 |
| 約定件数 | 15 | 479 |
| 新規 / 追加 / 縮小 / script決済 | 4 / 4 / 3 / 3 | 120 / 120 / 119 / 117 |
| SL決済 | 1 | 3 |
| 保有中Funding適用 | 2 | 60 |
| 最終建玉 | 0 | 0 |
| 費用込み資産増減 USDT | -6.028136 | -58.074424 |
| 再開結果・連続実行の一致 | 完全一致 | 完全一致 |

両方とも `status=ok`、`market_data_complete=true`、`pending_safe_close=false`。月間のFunding適用はLONG 30件、SHORT 30件。約定数量合計は0、手数料26.3375607507888600、Funding合計-0.0236276819787で台帳と一致した。月間の判断statusはhold 8,160、no_op 4、planned 476で、不正応答や拒否はなかった。

月間の再開版は7,554判断を保存した後に実行プロセスが終了した。孤立したmodel requestがないことを確認し、入力/config fingerprintを照合して保存tickから残りのみ再開した。完了後に全結果が連続実行と一致し、原子的に完成ディレクトリを公開した。中断原因自体は特定していない。

成果物は `data/real-replay-20250901/` と `data/real-replay-202509/` のreport/config/decisions/events/SQLite。設定SHA-256はそれぞれ `2a7b27cdaa653028fa677c7303739667a3c4f49415c76342cd15fdcd61ae52e1`、`a20073b8fa09e252fa83fc1840969dab825afbbd6a0dda05ba2d0c12fdb8952c`。生データとDBはGit除外、再実行コードと本要約を管理する。関連テストはcore・位置管理66件、既存Hyperliquid185件、合計251件成功した。

## 解釈上の制約

Funding rateは実測値だが、価格はminute openで近似する。端数msの決済時刻は次の分足終了tickへ送るため、厳密な決済時点の建玉・mark price・同時刻SLの順序を再現しない。保護決済もOHLCベースである。Binance入力はHyperliquidの板・約定・Funding条件とは異なる。今回の損益をLLMの成績や実運用収益と解釈しない。

## 次の実施順序

1. Fundingの決済時刻・mark price、確定足利用時刻、遅延中のSLを扱うイベント契約を確定する。残る旧データ取得経路は共有coreへ段階的に移し、既存成果物との互換性を維持する。
2. 時系列equity、含み損込みDD、保有時間、turnover、flatからflatまでの取引単位の集計を追加する。同じ入力・費用・Riskのno-tradeと単純ルールを対照として固定する。
3. Gemini通常APIの少数canaryを、モデル・prompt・schema・token上限・総予算を固定して実施する。今回の実行に有料APIは含まれないため、予算を定めてから接続する。応答遅延・無効応答・送信後クラッシュの復旧を優先して確認する。
4. 未使用期間で、LLMのopen/hold/closeと増減許可を比較する。全見送り、少数取引、API費用を含む不十分なサンプルはinconclusiveとする。学習済みモデルの過去相場記憶もあるため、歴史Replayだけで採用しない。
5. Hyperliquid同venueで将来shadowを実施する。まず24時間の機能確認、その後は開始前に固定した評価期間を使う。Testnetでは注文・SL・部分約定・再送・口座照合を確認し、研究の採否と分けて扱う。
