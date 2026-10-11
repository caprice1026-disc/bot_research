# 指定リポジトリの取得と戦略ロジック調査

調査対象は指定URLの重複を除いた4件。2026-10-04に公開ソースをコミット固定で取得し、静的に調査した。上流コード、インストールスクリプト、取引処理は実行していない。全ファイルのSHA-256は `source_manifest.json` に保存した。`python references/fetch_sources.py` で同じソースを取得できる。ソースとZIPは `.gitignore` の対象とし、入れ子のGitリポジトリは作成していない。

| リポジトリ | 固定コミット | ファイル数 | ソースに記載されたライセンス |
|---|---|---:|---|
| evan-kolberg/prediction-market-backtesting | `c76e77af00ef53472a9da8f66dae7fdd2d3e5928` | 254 | 複数ライセンス。戦略と拡張はLGPL-3.0-or-later、該当しないファイルにはMIT。`LICENSE`、`NOTICE`に対象一覧 |
| alsk1992/CloddsBot | `c930628c47a4a745f497e29035a3b64622f97cc7` | 837 | MIT |
| ent0n29/polybot | `51e5362f026adf54162fc1256f453241d93680e6` | 238 | MIT |
| yangyuan-zhen/PolyWeather | `43e658bbf6286e3cccdf4f09ce478ec90b3fb960` | 877 | AGPL-3.0-only |

ライセンスの根拠: [prediction-market-backtesting LICENSE](https://github.com/evan-kolberg/prediction-market-backtesting/blob/c76e77af00ef53472a9da8f66dae7fdd2d3e5928/LICENSE)、[NOTICE](https://github.com/evan-kolberg/prediction-market-backtesting/blob/c76e77af00ef53472a9da8f66dae7fdd2d3e5928/NOTICE)、[CloddsBot LICENSE](https://github.com/alsk1992/CloddsBot/blob/c930628c47a4a745f497e29035a3b64622f97cc7/LICENSE)、[polybot LICENSE](https://github.com/ent0n29/polybot/blob/51e5362f026adf54162fc1256f453241d93680e6/LICENSE)、[PolyWeather README 56–64行](https://github.com/yangyuan-zhen/PolyWeather/blob/43e658bbf6286e3cccdf4f09ce478ec90b3fb960/README.md#L56-L64)。この研究では考え方と公開数式を出典として使い、上流実装を丸ごと実行することは避ける。実装を複製・改変して配布する場合には対象ファイルの条件を引き継ぐ。

## 再利用できる戦略・検証ロジック

| ロジック | 固定ソース | 研究への適用条件 |
|---|---|---|
| YES/NOの合計購入費用と手数料が1ドル未満の完全セット | [binary_pair_arbitrage.py 68–132行](https://github.com/evan-kolberg/prediction-market-backtesting/blob/c76e77af00ef53472a9da8f66dae7fdd2d3e5928/strategies/binary_pair_arbitrage.py#L68-L132)、[polybot QuoteCalculator.java 167–175行](https://github.com/ent0n29/polybot/blob/51e5362f026adf54162fc1256f453241d93680e6/strategy-service/src/main/java/com/polybot/hft/polymarket/strategy/service/QuoteCalculator.java#L167-L175) | 両側の同時刻askと数量が必要。二脚は同時確定しないため、片脚約定を無視した無リスク利益とは扱えない |
| 締切直前の優勢側を買って確定まで保有 | [late_favorite_limit_hold.py 202–211行](https://github.com/evan-kolberg/prediction-market-backtesting/blob/c76e77af00ef53472a9da8f66dae7fdd2d3e5928/strategies/late_favorite_limit_hold.py#L202-L211) | 残り時間、ask上限、bid支持、spread、深さを確認。後から判明した勝者を選ばない |
| 急落後の反発・平均回帰 | [panic_fade.py 123–148行](https://github.com/evan-kolberg/prediction-market-backtesting/blob/c76e77af00ef53472a9da8f66dae7fdd2d3e5928/strategies/panic_fade.py#L123-L148)、[Clodds strategies.ts 205–260行](https://github.com/alsk1992/CloddsBot/blob/c930628c47a4a745f497e29035a3b64622f97cc7/src/strategies/crypto-hft/strategies.ts#L205-L260) | 新情報による確率変化と一時的な価格変化を区別。spotが静かな条件、最大保有時間、損切りを固定 |
| 原資産変動に対する予測市場の追随遅延 | [Clodds strategies.ts 110–177行](https://github.com/alsk1992/CloddsBot/blob/c930628c47a4a745f497e29035a3b64622f97cc7/src/strategies/crypto-hft/strategies.ts#L110-L177) | spotと予測市場を過去方向に時刻結合。元の `0.50 + abs(movePct)/100*5` は説明用の線形近似であり、校正済み確率とは扱わない |
| 天候予測確率と市場価格の差 | [Clodds edge.ts 92–120行](https://github.com/alsk1992/CloddsBot/blob/c930628c47a4a745f497e29035a3b64622f97cc7/src/weather/edge.ts#L92-L120)、[PolyWeather deb_probability.py 1–17行](https://github.com/yangyuan-zhen/PolyWeather/blob/43e658bbf6286e3cccdf4f09ce478ec90b3fb960/src/analysis/deb_probability.py#L1-L17) | 予測発表時刻、決済観測地点、単位、整数丸めの一致が必要。手数料と約定差を引いた後の差で判断 |

polybotの保有偏りに応じた価格調整と資金上限も参考になる。[QuoteCalculator.java 71–145行](https://github.com/ent0n29/polybot/blob/51e5362f026adf54162fc1256f453241d93680e6/strategy-service/src/main/java/com/polybot/hft/polymarket/strategy/service/QuoteCalculator.java#L71-L145)では残高・注文上限に基づいて数量を制限し、保有量の少ない側の買い価格を改善する。

## データ取得経路

prediction-market-backtestingには無料PMXTの時間単位L2アーカイブとTelonexのAPI/ローカル経路がある。[data-vendors.md](https://github.com/evan-kolberg/prediction-market-backtesting/blob/c76e77af00ef53472a9da8f66dae7fdd2d3e5928/docs/data-vendors.md)のPMXT経路は `r2v2.pmxt.dev` と `r2.pmxt.dev`、ファイル名は `polymarket_orderbook_YYYY-MM-DDTHH.parquet`。記載上の起点は2026-02-21T16:00:00Z。欠損時間の後は新しいsnapshotまで差分を適用しない。TelonexはAPIキーを要求する。アーカイブの現在の提供範囲は実取得で別途確認する。

CloddsBotの標準バックテストはローカル取引ログから価格系列を作る。[backtest.ts 223–273行](https://github.com/alsk1992/CloddsBot/blob/c930628c47a4a745f497e29035a3b64622f97cc7/src/trading/backtest.ts#L223-L273)。polybotはClickHouseに収集した市場・ユーザー取引と板を前提とし、[research/README.md](https://github.com/ent0n29/polybot/blob/51e5362f026adf54162fc1256f453241d93680e6/research/README.md)に収集・品質・約定再現の検証コマンドがある。これらに完全な取引履歴データセットは同梱されていない。

PolyWeatherの [data/probability_training_snapshots.jsonl](https://github.com/yangyuan-zhen/PolyWeather/blob/43e658bbf6286e3cccdf4f09ce478ec90b3fb960/data/probability_training_snapshots.jsonl) は188行、うち117行は `test_city`、`(city,timestamp)` 重複は148行。日付範囲は2026-03-04～2026-04-15。価格・token ID・決済結果は含まれず、損益検証の市場データとして採用しない。READMEには本番DB、内部mispricing、数量決定、取引Bot実行コードを公開対象外と明記している。

## 上流バックテストをそのまま利益の証拠にできない理由

1. **CloddsBotの同一tick約定**。[backtest.ts 764–775行](https://github.com/alsk1992/CloddsBot/blob/c930628c47a4a745f497e29035a3b64622f97cc7/src/trading/backtest.ts#L764-L775)ではtickを見て判断した直後、同じtick価格で約定する。板はcontextに渡されるが約定価格はtickから取り、待ち行列・反応遅延・ask/bid差・数量を再現しない。一定率の手数料は現行市場の価格依存手数料とは別に検証する必要がある。
2. **polybotのターゲット約定条件付き再現**。[strategy_backtest.py 480–496行](https://github.com/ent0n29/polybot/blob/51e5362f026adf54162fc1256f453241d93680e6/research/backtest/strategy_backtest.py#L480-L496)は対象トレーダーが約定した価格との比較で自分の約定を推定する。自身の待ち行列と独立した市場全体のエントリー機会を検証していない。同梱 [backtest_report.json](https://github.com/ent0n29/polybot/blob/51e5362f026adf54162fc1256f453241d93680e6/research/backtest/backtest_report.json) の自分の仮想損益は -126.7975。対象トレーダーの利益を、この戦略の利益と読み替えない。
3. **L2からの約定待ち推定の限界**。prediction-market-backtestingの [execution-modeling.md 108–130行](https://github.com/evan-kolberg/prediction-market-backtesting/blob/c76e77af00ef53472a9da8f66dae7fdd2d3e5928/docs/execution-modeling.md#L108-L130)は約定tickで待ち数量を減らすが、真のFIFOや自分の前方取消を再構成できない。maker rebateの1ドル支払下限も未モデル化（同60行）。この研究では確認できないmaker報酬を利益に加算しない。
4. **天候精度と損益の違い**。PolyWeatherの [deb_evaluation.py 782–870行](https://github.com/yangyuan-zhen/PolyWeather/blob/43e658bbf6286e3cccdf4f09ce478ec90b3fb960/src/analysis/deb_evaluation.py#L782-L870)は過去日付だけで重みを推定する点を参考にできるが、最高気温の誤差を評価しており、価格・手数料・約定を評価していない。CloddsBotの温度確率は [edge.ts 266–289行](https://github.com/alsk1992/CloddsBot/blob/c930628c47a4a745f497e29035a3b64622f97cc7/src/weather/edge.ts#L266-L289)の固定3°F幅・5/25/50/75/95%階段であり、検証済みの気温分布ではない。

この調査から得られるのは戦略候補と実装・検証の出発点である。現行仕様への適合、実際に取得した期間の充足、コスト控除後の成績は、研究runnerの実行結果で判定する。
