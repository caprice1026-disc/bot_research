# 天気予報差の再現性と利益要因を検証する

このExecPlanはルートの `PLANS.md` に従い、作業中に進捗・発見・判断・結果を更新する。

## Purpose / Big Picture

前回のChicagoの天気予報差は27約定で17.95562ドルの利益だったが、30日しかなく、板の鮮度を5秒以内にすると損失へ変わった。ユーザーの「より詳しく研究」に応え、利益の集中、予報確率の正確さ、費用・数量・執行条件への依存、新たな期間・地点での再現性を実データで調べる。成果物は既存の `polymarket-strategy-lab/` 内に追加する。前回の入力・結果は保存する。実注文は研究の範囲に含まれない。

主要条件は前回の2モデル、過去30日の校正、午前10時判断、期待差8ポイント、5株、遅延60秒、価格差1セント、板鮮度60秒を維持する。地点を追加するときは同じ手順を各地点の過去30日に適用する。新しい結果を見て閾値を選び直さない。再現性がない場合も、その結果を完成した研究として残す。

## Progress

- [x] (2026-10-05) 規約、既存runner、設定、前回結果、メモリの出典を確認。
- [x] (2026-10-05) 現行手数料とSingle Runsの提供開始を公式資料で確認。Chicagoの6月・7月市場は存在するがv3板は404。10月3日の実板は206で取得できる。
- [x] (2026-10-05) 新たな地点NYC/KLGAとAtlanta/KATLの市場・決着地点・NOAAルールを確認。以下の評価範囲を損益計算前に固定。
- [x] (2026-10-05) 前回27約定の利益集中、予報誤差、確率と公式決着、板鮮度による取引集合の変更を分解した。
- [x] (2026-10-05) 新たな72都市日・792市場・1,584 outcomeについて9,504状態を取得。155部分Parquet・3,107,330行の同一性を検査した。初期seed欠損0。
- [x] (2026-10-05) 固定主条件と全51ケースを実行。908約定の数量・費用・払戻し・現金を照合し、都市損益を34暦日に集約した。
- [x] (2026-10-05) 主結果を見た後に7日平均の探索的対照を追加。69約定を別途照合し、同じ34日の日次差の区間を計算した。
- [x] (2026-10-05) 全52回帰テストと既存成果物検証が成功。日本語研究レポート、CSV、3図のPNG/SVG、READMEの導線を完成した。

## Surprises & Discoveries

公開アーカイブのmanifest APIは2026-10-05の確認で503を返したが、`dl.pendulumflow.com` の10月3日と既存8月31日のParquetはRange 206を返す。manifestの障害を実板の不存在と混同しない。配布元hashが取得できない新原本は、その制約を記録し、選択して保存した部分Parquetのhashを検証する。6月1日と7月1日のv3原本は404であり、そこを主な売買検証期間には選ばない。

NYCの決着地点はLaGuardia/KLGA、AtlantaはHartsfield-Jackson/KATLで、両都市とも夏時間UTC−4。ChicagoはUTC−5。この研究期間は夏時間内で、地点ごとの現地午前10時をそれぞれ14 UTCと15 UTCに対応させる。市場の指定地点を都市中心点へ置き換えない。

8月23日のNOAAへの移行文面は「Show Hourly Data」を持たず、最初の取得guardで3校正都市日が失敗した。観測頻度が未指定というルール差を保存するように修正し、成功済みcacheを再利用して失敗分を取得した。期間を短縮せず全162メタデータ都市日を保持した。

NYCの9月12日・9月30日はIEM定時観測23時間で、補間や都合のよい除外をしない。校正と評価でIEM最高気温が公式勝者区間に一致しない5都市日を記録した。払戻しは一貫して公式決着を用いる。

前回の5秒鮮度での赤字は、元の約定のうち5秒を満たす6件の損益+2.23560と、新しく選んだ3件の損益−4.37228の和だった。鮮度比較による取引集合の変化を、全利益が古い板に依存した証拠と取り違えない。

## Decision Log

Decision: 新たな評価はChicago 2026-09-30〜10-03の4日、NYCとAtlanta 2026-08-31〜10-03各34日、合計72都市日とする。各都市の校正は8月1〜30日の30日。
Rationale: Chicagoの後続期間だけでは4日しか増えない。結果を見る前に2つの米国地点を追加し、同じルールが地域を移しても機能するか調べる。新都市は過去データによる地域外検証で、将来のライブ成績とは異なる。
Date/Author: 2026-10-05 / Codex

Decision: 前回のChicago30日を利益要因の分析に使い、新しい72都市日の採否集計と分ける。都市間の気象の相関を考慮し、合算のbootstrapは暦日をまとめて3日ブロックで再標本化する。
Rationale: 同じ日に複数都市で取引しても独立標本とは限らない。前回見た日を新規標本へ加えて確証を増やさない。
Date/Author: 2026-10-05 / Codex

Decision: 大きな取引フレームワークを追加せず、既存のBook復元、校正、売買判定、現金集計を再利用する。新しい取得・解析は `weather_deep_research.py`、固定条件は `research/weather_deep/config.json`、取得は `data/weather_deep/`、結果は `results/weather_deep/`、説明は `research/WEATHER_DEEP_REPORT.md` に置く。
Rationale: 前回の計算との比較を保ち、原本を変えずに再実行できる形にする。
Date/Author: 2026-10-05 / Codex

## Outcomes & Retrospective

追加研究を完了した。主条件は新たな64約定で+35.60954ドル、3日ブロックの合算95%区間[1.38324,67.96112]。Chicago後続4日は−0.33140、NYC34日は+15.78206、Atlanta34日は+20.15888で、都市別区間は全てゼロを含む。前回Chicago30日を新たな成績へ混ぜていない。

主要結果の後に追加した過去7日の気温平均は69約定・+27.92558ドル。予報モデルとの差は+7.68396ドル、同日で対応させた差の95%区間[−5.40544,21.25928]であり、予報の追加価値は確認できない。新しい板の利益機会は研究候補として残るが、ライブ約定・大きい数量・将来収益を認定しない。

鮮度5秒では新合算21約定・+7.20600ドル、区間はゼロを含む。大口条件も数量に応じて候補が変わり、区間が広い。静かな板の継続、当時のtick/minimum、競合注文、予報の実際の公開時刻が今後の検証課題。詳細と再実行手順は `polymarket-strategy-lab/research/WEATHER_DEEP_REPORT.md` に保存した。

## Context and Orientation

作業ルートは `C:\Users\Hodaka\Downloads\div\bot_research`。研究環境は `Polymarket_5m_btc/.venv/Scripts/python.exe`。`polymarket-strategy-lab/weather_backtest.py` は気温区間の確率と過去だけの予報校正を持つ。`weather_full_book_backtest.py` の `run_scenario` は判断時点の候補順位を決め、60秒後の実askと数量で仮想約定し、公式決着まで資金を拘束する。`btc_data.py` の `Book` は、天気向けone-sided opt-inを持つ実板復元器である。`weather_book_check.py` は実Parquet行をBookイベントへ変換する。

板は売買価格と数量を示す。表示された5株を現実の注文で取れることまでは証明しない。鮮度はその情報の更新から経過した秒数で、静かな板が接続継続中に残っていたという仮定を別途含む。予報校正は過去の最高気温と予報の差から重み・平均ずれ・誤差幅を求めること。bootstrapは日付の連続性を一部残したまま過去の日次損益を再抽出し、結果のばらつきを表示する方法である。

## Plan of Work

第一の節目で前回のprimary27約定と全候補を読み、Yes/No、価格帯、週、期待差、最大勝ち日除外、予報誤差、予報確率の二乗誤差を集計する。日付単位のleave-one-outを保存する。これらは前回標本の説明で、未使用期間の検証とは扱わない。

第二の節目で新地点とChicago後続日のGamma市場、Single Runsの00 UTC初期予報、IEM定時METAR、公式Data API v2決着を取得する。初期予報は6時間後に利用可能という前回仮定を維持する。全ての区間のYes/Noを取得し、期待差や実際の勝者で対象市場を選ばない。前1時間と判断後6分の実板を取り、初期snapshotが足りない場合だけ前24時間までさかのぼる。未取得・欠損は別の状態として残す。

第三の節目で各都市を前回と同じrun_scenarioへ渡す。主条件と別に、板鮮度5/15/30/120/300秒、実行遅延1/30/120/300秒、価格差2.5/5セント、数量10/25/50株、期待差4/12ポイントを探索的に比較する。費用だけの影響は同じ基本約定を固定して再計算する。感度条件の中で成績のよい条件だけを採用しない。

最終の節目で原本・部分ファイルと設定のhash、token、判断前受信、数量、手数料、公式払戻し、現金、日次集計を検証する。予報の単一モデルと同重みモデルは確率の診断として比較し、別の利益条件を増殖させない。JSONとCSV、日本語レポート、標準の静的図を保存する。

## Concrete Steps

ルートから次のコマンドを使う。collectorは成功済みの取得を再利用し、途中経過を保存する。

    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_deep_research.py collect
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_deep_research.py books
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_deep_research.py evaluate
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_deep_research.py verify
    .\Polymarket_5m_btc\.venv\Scripts\python.exe -m unittest discover -s polymarket-strategy-lab/tests -v

## Validation and Acceptance

新たな主要条件の結果が72都市日全てについて記録され、未取得とゼロ取引が区別されること。回帰確認では都市ごとの時刻、future snapshotの除外、予報の過去だけの校正を確認する。全ての仮想約定を原snapshot・公式決着へ対応させ、手数料と損益と現金を別計算で照合する。既存研究の入力hashを保存し、前回の結果を上書きしない。収益性が否定・不確実でもデータ条件と理由を揃えて報告する。

## Idempotence and Recovery

新規取得は `data/weather_deep/` 内で行う。既存のChicagoイベント・予報・決着・板は読み取りだけで再利用する。新しい設定は損益前に保存し、取得条件やURLが違うcacheは再利用しない。配布APIが不調でも直接公開Parquetの読み込みを確認し、失敗分はmanifestに残して限定的に再試行する。

## Artifacts and Notes

前回利益は17.95562、27fills、95%区間[−1.04438,37.36698]。これを追加研究の開始値とする。前回の47回帰テストは実装の正常性の証拠であり、収益性の証拠には置き換えない。

## Interfaces and Dependencies

公開HTTP、JSON/CSV、統計の基本計算は標準ライブラリを使う。Parquetは既存DuckDB1.5.6と署名済httpfsを再利用する。Pythonの時刻は明示的なUTCか対象都市の固定夏時間offsetを使い、DSTをまたぐ拡張は拒否する。新規collectorの出力にrequested、observed、errors、SHAを持たせる。既存APIやrunnerのglobal設定を変更しない。

更新記録: 2026-10-05 新たな評価範囲と主条件、探索的分析、API障害への取得経路、完了条件を損益計算前に固定した。

更新記録: 2026-10-05 取得・評価・統計・対照比較・52テスト・原本照合を完了し、正の条件付き損益と未確認の予報追加価値を分けて記録した。
