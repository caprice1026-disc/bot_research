# GEFS全市場日・PIT候補・Level 1/2バックテスト継続実装

このExecPlanは `PLANS.md` に従う継続計画であり、実装中に更新する。目的は、PolymarketのNew York気温市場について、同じ対象日を共有する市場ルールを一つのGEFS予報へ結合し、観測とCLOB価格をPoint-in-Time（取引時点で知り得た情報だけを使うこと）として候補化することである。

## 目的と確認方法

利用者は `weather-polymarket-research` の正規化JSONLから、予報・観測・価格の期間が揃った市場だけを再現可能なバックテストへ渡せる。GEFSのGRIB2本体は全ファイルを取得せず、`.idx`でTMAX 2 m above groundのメッセージ範囲を選び、HTTP Rangeで必要部分だけを取得する。取得済みのindexとmessageは `data/raw/gefs-cache/` に保存し、再実行では再利用する。

確認コマンドはリポジトリ `weather-polymarket-research` 直下で次のとおりである。

    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe -m weather_research.cli run-backtest --data-dir data --results-dir results --level both --report reports/backtest_report.md
    .\.venv\Scripts\python.exe -m weather_research.cli validate-results --results-dir results

観測が無い期間や過去のbest askが無い価格履歴では、成功やPnLを推測せず `insufficient_data` と記録する。

## 進捗

- [x] (2026-08-09) GEFSの`.idx`解析、TMAXメッセージのRange取得、eccodesによる最近傍格子点の復号を実装した。
- [x] (2026-08-09) GEFS HTTPの429/5xx・通信失敗に対する最大3回の指数バックオフ再試行と、index/messageの内容アドレス型キャッシュを実装した。
- [x] (2026-08-09) 市場ルールの重複を対象日へ集約し、12Z issue cycleを全対象日へ適用する再開可能な `collect-gefs-market-days` を実装した。
- [x] (2026-08-09) ensemble bucket確率、Gaussian近似、NCEI日次最高気温、CLOB価格をPIT候補へ結合した。
- [x] (2026-08-09) 履歴価格をLevel 1の価格プロキシ、best askをLevel 2の実約定側価格として区別し、手数料・スリッページをバックテストへ適用した。
- [x] (2026-08-09) 正規化アーティファクトを読む `run_backtest_from_artifacts` とCLIのLevel 1/2/both分岐を実装した。
- [ ] 全46対象日について21 memberを実ネットワークで完走し、GEFS・観測・価格の共通期間を確定する。既定CLIは全対象日を指定するが、ネットワーク取得は数千回のRange要求になるため、再試行・キャッシュ・`--max-days`で中断再開できる。
- [ ] 共通期間が得られた後にLevel 1/2を再実行し、`results/backtest_summary.json`、`results/trades.csv`、レベル別成果物を更新する。

## 実装方針

`collectors/gefs_grib.py` はGEFS低レベル取得だけを担当する。`collection.py` の `collect_gefs_market_days` は `market_rules.jsonl` を読み、同じ対象日の7個以上のbucketを一つにまとめ、都市設定の緯度経度で各memberの対象日最高値を生成する。キャッシュは動的データなのでリポジトリへ追加しない。

`candidate_builder.py` は、対象日・観測局・市場tokenをキーに正規化レコードを結合する。取引時刻より後に公開された予報は除外し、各memberの最新issueを選ぶ。観測値は市場説明にある整数°F解像度へ丸めて結果bucketを決める。予報のensemble確率を戦略確率とし、同じmember値からGaussian近似も保存する。

CLOB履歴の `p` は過去の板のaskではないため、best askが無い点をLevel 2へ流さない。Level 1では `p` を明記したhistorical price proxyとして扱い、設定したspread・slippage・feeを適用する。best askを持つ点ではaskをそのまま基準にし、spreadを二重計上しない。

## 発見事項

- GEFSの対象日最高値は単一の予報時刻ではなく、対象ローカル日のUTC境界に重なる複数の3時間TMAX区間の最大値で求める必要がある。
- Windowsではeccodesのfile系APIへ `BytesIO` を渡すとfilenoエラーになったため、Range取得した単一GRIBメッセージは `codes_new_from_message` で復号する。
- NCEI取得はHTTP 200でも要求期間全体を返すとは限らない。現在の保存観測は2025-08-09から2025-08-27までで、2026-01-06のGEFS/CLOBパイロットと共通ではない。
- CLOBの現在板APIで得られるaskを過去履歴へ遡及して埋めることはできないため、過去Level 2が不足すること自体を成果として記録する。

## 判断記録

- issue cycleは既定12Z（対象ローカル日開始の前日側）に固定する。同じ条件で全対象日を比較でき、後から00/06/18Zを追加してもキャッシュキーとmanifestで区別できる。
- 全21 memberを既定とするが、テストと障害復旧では `--max-members` と `--max-days` を許可する。少数memberの結果は21 memberの完全な研究結果とは報告しない。
- 候補生成はcoverage gapが一つでもあればstrictに `insufficient_data` とする。部分的なPnLを全期間の性能と誤認しないためである。

## 成果物と完了条件

実行時の成果物は `data/normalized/market_rules.jsonl`、`forecast_members.jsonl`、`observations.jsonl`、`price_points.jsonl`、`results/dataset_manifest.json`、`results/source_audit.json`、`results/backtest_summary.json`、`results/trades.csv` である。レベル2のboth実行時は追加で `backtest_summary_level2.json` と `trades_level2.csv` を出力する。raw snapshotとGEFS cacheは動的データとしてignoreする。

完了は、全テストが成功し、全対象日の取得manifestが成功または個別エラーを列挙し、共通期間がある場合のみLevel 1/2のtrade/PnLを生成し、共通期間が無い場合は理由付き `insufficient_data` のまま検証コマンドが `valid: true` を返すことで判定する。

## 結果と振り返り

2026-08-09時点では実装と単日GEFS/CLOBパイロット、アーティファクト読込バックテストまで完了した。全46対象日21 memberの長時間ネットワーク取得と、観測期間不足の解消は未完了である。現在のCLIはこの不足を隠さず、Level 1/2とも0 trade・`insufficient_data`として保存する。
