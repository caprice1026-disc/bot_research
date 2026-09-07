# 2026-09-08 コードレビュー

対象は前回からのSQLite保存・市場購読・coverage選別・lead-lag分析の未コミット差分。

## 発見と対応

- P1: 保存workerが失敗してqueueを破棄した直後、待機中のproducerがqueueへ追加すると、closeのqueue.joinが終了しない。実際の保存失敗を使う回帰テストでTimeoutErrorを再現。closeはqueueの排出とworker終了の双方を待ち、worker失敗を報告する。submitも待機後に失敗を再確認する。
- P1: Polymarketのgap検査がshock時刻からしか始まらず、shockより前に切断が終わっても古い基準価格を使用できた。実際に選ばれた基準quoteの受信時刻からhorizonまでを検査する。
- P2: load_rowsのsource指定はSQLiteだけに適用され、JSONL/Parquet混在入力では系列に別sourceが混入した。すべての形式で同じsource filterを適用する。

既存のbatch rollback、bounded Parquet変換、市場別decision interval、支持データ保持、token変更時のみの再購読も確認した。

## 検証

- 新しい4ケース（source filterは2形式）で修正前の失敗を確認した。
- 修正後 pytest: 86 passed。
- Ruff: All checks passed。Pyright: 0 errors。
- 実データの2時間テストはmain反映後に新しいrunとして実施する。現時点で完走や市場データ品質を保証する結果ではない。

## 制約

lead-lag CLIは指定sourceのイベントをメモリへ読み込むため、大規模分析は期間を区切る必要がある。SQLiteからParquetへの変換はbounded batch。SDK内部のネットワーク再接続はアプリ側connection IDだけでは完全には識別できないため、受信gapとSDK dropの実測も併せて評価する。
