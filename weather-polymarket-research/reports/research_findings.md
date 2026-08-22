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

## GEFS/CLOBパイロット

追加の再実行可能CLIで、2026-01-06について前日12Z発行のGEFS 21メンバーと、同日の7バケット市場のCLOB価格履歴を取得した。

- GEFSメンバー: 21/21件、36.11〜38.06°F、平均37.14°F
- CLOB価格ポイント: 249件、7市場
- GEFS取得: `.idx`のTMAXメッセージだけをHTTP Rangeで取得し、KLGA最近傍格子点を華氏へ変換
- Point-in-Time: 公開履歴の運用可用性を保守的に `received_time=forecast_issue_time` と仮定

これは単一対象日の結合確認であり、全期間のバックテスト結果ではない。対象日の公式観測が不足しているため、勝敗・PnL・エッジの確定には使っていない。詳細は `results/gefs_forecast_manifest.json` と `results/price_history_manifest.json` に保存した。

## バックテスト判定

`backtest_summary.json` は `status=insufficient_data`、`reason=no backtest candidates`、`trade_count=0` である。これはゼロ損益の成功ではなく、予報メンバー不足のため候補生成を行わなかったという意味である。`validate-results` は `valid=true` を返す。

## 次の実装課題

1. GEFSの対象日・issue cycleを全市場日へ拡張し、取得済みメッセージのキャッシュと再試行を追加する。
2. GEFSバケット確率、Gaussian近似、観測結果を市場ルールへ結合し、Point-in-Time候補を生成する。
3. CLOB価格のask/midと手数料・スリッページを候補へ結合する。
4. 予報履歴・観測・価格の期間が揃った後にのみLevel 1/2バックテストを再実行する。

実行成果物は `results/dataset_manifest.json`、`results/source_audit.json`、`results/backtest_summary.json`、`results/trades.csv` と `data/normalized/` に保存している。rawスナップショットは再実行ごとに追加保存するが、リポジトリでは動的データとして除外する。
## 継続実装 2026-08-09

GEFSについて、`.idx`のTMAX 2 m above groundメッセージだけをHTTP Rangeで取得し、indexとmessageを `data/raw/gefs-cache/` にキャッシュする処理を追加した。429、5xx、通信失敗は最大3回まで指数バックオフで再試行する。`collect-gefs-market-days` は `market_rules.jsonl` の重複bucketを対象日へ集約し、既定12Z issue cycleを選択して中断後に同じ対象日・memberを再利用する。

`candidate_builder.py` はensemble bucket確率、Gaussian近似、整数°Fへ丸めたNCEI日次最高気温、CLOB価格をPIT候補へ結合する。履歴APIの `p` は過去askではないためLevel 1では価格プロキシ、Level 2ではbest ask必須として分離した。手数料、spread、slippageは `ExecutionConfig` と候補の観測quoteへ適用する。

現在の保存観測期間は2025-08-09から2025-08-27で、GEFS/CLOBパイロットの2026-01-06とは揃っていない。実データCLIのLevel 1/2はこの不一致を理由に `insufficient_data`、trade_count 0として `results/backtest_summary.json` とレベル2成果物へ保存した。実行したテストは61件成功、`validate-results` は `valid: true` である。
