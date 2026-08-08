# Polymarket天気市場研究・初回結果

## 結論

対象期間 2025-08-09〜2026-08-09 のNew York市場について、公開データの到達性、市場ルール、観測値、GEFSカタログを確認した。ただし、Point-in-Timeで使えるGEFSメンバーをまだ正規化できていないため、今回の判定は `insufficient_data` である。取引候補、損益、優位性の結論は出していない。

## 取得結果

最終取得時刻は `2026-08-08T17:05:53Z`。Polymarketの説明文に合わせ、決済観測所はLaGuardia（`KLGA`）、NCEI Global Hourly IDは `72503014732` とした。

| 項目 | 件数・範囲 |
| --- | --- |
| Polymarket検索結果 | 432市場 |
| 対象期間内の有効市場ルール | 425 |
| 期間外として除外した有効ルール | 7 |
| NCEI観測 | 605件 |
| NCEI観測範囲 | 2025-08-09T00:00:00Z〜2025-08-27T03:51:00Z |
| GEFS issue dates | 365日分のAWSカタログ |
| 正規化済みGEFSメンバー | 0件 |
| バックテスト取引 | 0件 |

NCEIはHTTP 200で応答したが、要求した1年全体の観測を返していない。GEFSもカタログの存在は確認できた一方、GRIB2本体のメンバー値はまだデコードしていない。したがって、観測値が得られたことを予報・確率・損益の結果と混同しない。

## バックテスト判定

`backtest_summary.json` は `status=insufficient_data`、`reason=no backtest candidates`、`trade_count=0` である。これはゼロ損益の成功ではなく、予報メンバー不足のため候補生成を行わなかったという意味である。`validate-results` は `valid=true` を返す。

## 次の実装課題

1. NOAA GEFS AWS上のGRIB2から、issue time・cycle・lead time・ensemble memberを保持してKLGA近傍へデコードする。
2. Polymarket CLOBの価格履歴をtoken単位で取得し、価格時刻が取引時刻以前であることを検証する。
3. GEFSバケット確率、Gaussian近似、観測結果を市場ルールへ結合し、Point-in-Time候補を生成する。
4. 予報履歴・観測・価格の期間が揃った後にのみLevel 1/2バックテストを再実行する。

実行成果物は `results/dataset_manifest.json`、`results/source_audit.json`、`results/backtest_summary.json`、`results/trades.csv` と `data/normalized/` に保存している。rawスナップショットは再実行ごとに追加保存するが、リポジトリでは動的データとして除外する。
