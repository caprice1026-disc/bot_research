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

## 2026-10-12 後続期間と予報サービス比較


ユーザーの再研究依頼を受け、同じ計画を継続する。前回の最終日は10月3日である。今回は米国現地で完了した10月4〜10日のChicago/KORD、NYC/KLGA、Atlanta/KATL、合計21都市日を後続評価に固定する。10月11日は収集開始時点で未完了なので含めない。利益を見て期間を縮めたり、取得失敗日をゼロ損益に置き換えたりしない。

### Progress


- [x] (2026-10-12 JST) 既存runner、前回結果、実行環境、公式API資料を確認した。
- [x] (2026-10-12 JST) 10月10日のGamma、Single Runs、Pendulum Flow Parquetの読取を確認した。HTTP200/200/206。
- [x] (2026-10-12 JST) 校正90都市日と後続21都市日の予報・観測・231評価市場の公式決着を取得。metadata完全、取得エラー0。
- [x] (2026-10-12 JST) 全21都市日・462 outcomeの板取得を完了。部分Parquet144本・714,428行、初期seed欠損0、取得エラー0。
- [x] (2026-10-12 JST) 前回fit固定、NBM、単独GFS、単独ECMWF、7日平均について、5案×8条件×3都市の120ケースを実行。全案の主要損益が負となった。
- [x] (2026-10-12 JST) 1,792入力ファイルのhashと593 scenario約定の会計・時点・数量を照合。57回帰テスト成功。日本語レポート、約定CSV、損失内訳JSON、README導線を完成。

### Milestones / Concrete Steps


第一段階では `polymarket-strategy-lab/research/weather_followup_20261012/config.json` を損益計算前に保存する。前回の8月1〜30日の校正、午前10時、00UTC runと6時間公開遅延、期待差8ポイント、5株、60秒遅延、1セント不利価格差、60秒板鮮度を維持する。主要結果では `results/weather_deep/summary.json` 内の前回fitをそのまま使う。比較にはNOAA/NWSの偏り補正済み複数モデル予報NBMをOpen-MeteoのSingle Runsから取得し、同じ校正期間だけで誤差分布を推定する。NBMはNWSの数値ガイダンスであり、人が調整した最終地点予報とは区別する。既存runnerのファイルや前回成果物は変更せず、新runner内の実行時設定だけを別出力先へ切り替える。

第二段階では `weather_followup.py collect` と `books` を実行する。原本は `data/weather_followup_20261012/` に保存し、成功キャッシュを再利用する。7日平均の対照には同地点の過去7暦日の観測を使い、現在・将来の観測は入れない。全対象日・Yes/No token・公式決着が揃ってから評価する。観測不足と公式決着との不一致は記録し、決済は公式勝者を使う。

第三段階では `evaluate`、`verify` と `python -m unittest discover -s polymarket-strategy-lab/tests -v` を実行する。結果は `results/weather_followup_20261012/` に保存する。主要結果とサービス比較、板鮮度・不利価格差の感度、日付を揃えた損益差の3日ブロック再標本化区間を計算する。7暦日しかない区間の統計的弱さを明記する。API利用料金は取引損益に含めず、サービス費用を払える上限を別に示す。料金不明や過去予報がないサービスを仮想的な収益値で埋めない。

実行Pythonは同梱の `C:\Users\81905\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`（3.12.14）を使う。標準Python3.10では公式APIの`+00`日時を解釈できないため。リポジトリルートでこのPythonを用いて `polymarket-strategy-lab/setup_runtime.py`、`-m pip install --target polymarket-strategy-lab/.probe-deps -r polymarket-strategy-lab/research/weather_followup_20261012/requirements.txt`、`polymarket-strategy-lab/weather_followup.py collect --workers 4`、同runnerの `books --workers 2`、`evaluate`、`verify` を順に実行する。追加依存は研究ディレクトリ内だけに置く。サンドボックス内で追加wheelの読取が拒否される環境では、取得時と同じ承認済みの実行環境でオフライン評価・テストを行う。

板収集はDuckDBの多数の列別Range要求で429となったため、新しい `weather_archive_ranges.py` を用いる。PyArrowで末尾メタデータを読み、market列の最小・最大値に一致する行グループだけを選び、グループごとに1つのHTTP要求へまとめる。選ばれたグループ内は元の市場ID・event_type・厳密な受信時刻範囲で再選別する。範囲応答の206、Content-Range、長さ、ローカルhashを検証し、429ではRetry-Afterに従って待つ。原本全体のhashは検証したと主張しない。旧collectorのsource hashやコードは変更しない。

### Validation and Acceptance


全21都市日の対象範囲、3都市の指定空港、校正と評価の分離、前回fit同一性、実ask数量、板時刻、遅延、公式払戻し、手数料、現金保存とSHA256を確認する。成果物正常性の成功は実約定や収益性の証明とは異なる。新規テストは校正に後続日の正解を混ぜないことと、7日平均の将来参照・欠損拒否を検証する。データ不充足の場合も正常性・評価可否を別項目として報告する。

### Surprises & Discoveries


現在のcheckoutには前回の大部分の生データとvenvがない。標準Pythonは3.10.9である。公式AccuWeather FAQは過去の予報を提供しないと明記しており、過去観測で予報履歴を代用できない。現在のPolymarket公式資料のweather費用は前回と同じrate0.05である。TLSは現在のcertifiで検証し、検証無効化は行わない。旧cohort検証に必要なChicago30日の市場情報と決着だけを公開APIから復元し、範囲選別の新規回帰を含む57テストが成功した。

NBMはChicagoとNYCの気温MAEを改善したが、NYCの損失は拡大した。前回モデルのNo13約定の平均推定勝率76.9%に対し実現勝率15.4%、NBMのNo13約定も75.1%に対し7.7%。最低2°Fの標準偏差では、平均が2°F幅の区間の中央でもYes確率は最大38.3%になる。NBMがNYC10月5日の正解区間70–71°F内を予報しながらNoを買って損失となった例を確認した。これは結果後の診断で、同じ期間を使った確率幅の最適化は行わない。

後続21都市日の定時観測は各24時間で、公式勝者との不一致は0。校正期間には4都市日の不一致が残り、7日平均が参照するNYC9月30日の観測は23時間である。補間せず記録し、公式決着を払戻しに用いた。

### Decision Log


2026-10-12: 主要戦略のfitは前回結果に固定し、比較モデルだけ同じ8月校正期間から推定する。後続期間を見てモデルや売買閾値を選び直さないため。予報サービスの比較は今回追加の探索的評価と明示する。

2026-10-12: 更新原本は取得条件ごとに別rootへ保存する。旧データ不足を解消しても前回の結果とhashを変更しないため。公開予報サービスは公式資料・履歴取得可否・同地点の評価誤差で検討し、ブランド全体の精度主張を取引優位性へ読み替えない。

2026-10-12: 全比較案が損失のため、現条件の実運用・NBMへの変更・有料サービスの採用を支持しない。AccuWeatherとWeather Companyは発表時刻を確認できる当時の予報と利用条件が揃っておらず、損益未評価とする。7日間の損益を月次・年次へ換算せず、都市別の最良モデルを事後採用しない。

### Outcomes & Retrospective


後続研究を完了した。前回モデル17約定・−22.90699ドル、NBM17約定・−30.70709ドル、GFS単独19約定・−11.11635ドル、ECMWF単独18約定・−21.63663ドル、7日平均18約定・−22.29231ドル。前回モデルと7日平均の差は−0.61468ドル、3日ブロック95%区間[−7.19296,5.96664]であり、予報の追加価値は今回も確認できない。NBMの前回モデルに対する差は−7.80010ドルで、料金ゼロでも改善していない。

取得範囲は校正90＋後続21都市日、231評価市場・462 outcome。全評価日は取得済みで、欠損のためのゼロ損益を成功に置き換えていない。1,792ファイルのSHA256、593 scenario約定の独立Decimal会計、57テストが成功した。主な残る制約は7暦日しかない標本、予報の実際の公開時刻、表示板での条件付き約定、当時のtick/minimum情報不足、校正時の観測不一致と対照の23時間観測、原本全体の配布hash未照合、サービス料金未算入である。

成果物は `polymarket-strategy-lab/research/WEATHER_FOLLOWUP_20261012_REPORT.md`、`results/weather_followup_20261012/summary.json`、`verification.json`、主要約定CSVと`loss_attribution.json`。今回の条件付き損失を保存し、次の確率校正・サービス比較は今回と重ならない期間で検証する必要があると結論した。

更新記録: 2026-10-12 後続21都市日とNBMを含む予報サービス比較を既存計画に追加した。

更新記録: 2026-10-12 取得・5案の評価・損失分解・57テスト・入力と会計照合・日本語レポートを完了。利益の再現失敗と予報サービスの追加価値未確認を記録した。
