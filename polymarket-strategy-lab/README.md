# Polymarket 5戦略の実データ検証

指定された4リポジトリを調査し、5つの戦略を実装した研究用runnerです。各戦略の売買条件、入力データ、約定の仮定を固定し、現在の手数料で過去データを再生します。実注文は発注しません。採否はバックテスト結果と制約をまとめた [REPORT.md](REPORT.md) で判断します。

## 戦略と出典

| 戦略 | 売買条件 | 利用した設計 |
|---|---|---|
| 1. 完全セットの割安買い | UpとDownのask合計に手数料・各脚1セントの不利な価格差を加えても、受取1ドルとの差が2セント以上あるときに両側を購入。各脚に1秒の実行遅延を置き、片脚だけ約定した損失も計上 | prediction-market-backtestingのbinary pair arbitrage、polybotの板鮮度・資金上限 |
| 2. BTCのTWAP確率差 | 過去120秒の実測変動から最終60秒平均の確率を推定し、手数料・価格差控除後の期待差が6ポイント以上なら購入。残り30〜240秒、ask 0.10〜0.85 | prediction-market-backtestingのBTC snapshotモデル、CloddsBotの外部価格と市場価格の差 |
| 3. 終了間際の優勢側追随 | 残り20〜60秒、bid 0.80以上、ask 0.97以下、直近30秒のbid上昇が3セント以上、現物の期間内変化が同方向なら購入 | prediction-market-backtestingのlate favorite、polybotの古い板の除外 |
| 4. 天気予報の確率差 | Chicago KORD向けGFS・ECMWFの過去の予報runから最高気温の分布を推定。午前10時に、購入費用控除後の期待差8ポイント以上の区間を選択 | CloddsBotのweather edge、PolyWeatherの観測地点・整数気温区間・過去データだけの校正 |
| 5. 観測済み気温によるNO買い | 公開遅延30分を置いたKORD観測の当日最高気温が、区間上限を2°F以上超えたら、その区間のNOを検討。期待差1.5ポイント以上、1日1試行 | PolyWeatherのmax-so-far制約、CloddsBotのコスト控除後の選択 |

閾値は評価前に [btc_config.json](btc_config.json) と [weather_config.json](research/weather_config.json) に保存しました。BTCの後半期間、天気の後半30日を別に評価します。短い既存BTC記録で戦略の閾値を最適化せず、同じ条件で30日分へ検証を拡張しました。これは今回の作業中に結果を見る前に固定した記録であり、第三者への事前登録ではありません。後述の天気の板による追試は、同期間の初回結果を見た後の探索的評価です。

固定コミット、使用箇所、ライセンス、上流バックテストの限界は [SOURCE_AUDIT.md](references/SOURCE_AUDIT.md) に記録しています。CloddsBotの重複URLは一度だけ取得しています。ソースは静的に調査し、この研究のrunnerとして再実装しています。上流の実取引・セットアップ処理は実行していません。

## データと現行仕様

BTCの既存記録は2026-09-07の約2時間です。実際の受信時刻順に板を復元し、接続変更、5秒超の受信途絶、復元した板と配信最良気配の不一致があれば新しい全板まで無効にします。この記録に加え、2026-09-01〜09-30の毎日16:00〜17:00 UTCという固定時間帯を公開板アーカイブから取得しました。30時間枠・360市場・107,849行であり、全日24時間の検証ではありません。

拡張データの原本は [Pendulum Flowの公開アーカイブ](https://archive.pendulumflow.com)（CC BY 4.0）です。必要な市場のParquet行だけを読み、保存した部分データのSHA256を検査します。配布側の全ファイルhashは記録しますが、数十GBの全原本を再取得して照合した結果とは扱いません。アーカイブの受信時刻は配布側の時刻です。

既存BTC記録にはCoinbaseとChainlink 60秒TWAPが含まれます。拡張データでは実際のBinance 1秒足を、足確定の1秒後から利用可能と仮定します。そこから計算する60秒平均はChainlinkの代理変数です。両データを同一の観測条件として集計せず、真の決済結果は公式市場の勝者tokenで別に確認します。

天気は2026-08-01〜09-29の60日、Chicago KORDを対象とします。前半30日で予報誤差を校正し、後半30日で検証します。[Open-Meteo Single Runs API](https://open-meteo.com/en/docs/single-runs-api) のrun指定で当時の予報を取得し、モデルの開始時刻から6時間を公開遅延として置きます。観測値はIEMの同じKORD地点の定時METARです。決済元は期間中にWundergroundからNOAAへ変更されるため、市場文面を日別に保存します。

手数料は [公式資料](https://docs.polymarket.com/trading/fees) の `株数 × rate × p × (1-p)` を使用します。rateはBTC 0.07、天気0.05です。現在の取引コストを過去に適用する比較であり、各過去約定に実際に課された費用の復元ではありません。V2の購入手数料が担保通貨で別途徴収されることは [公式コントラクト](https://github.com/Polymarket/ctf-exchange-v2/blob/main/src/exchange/mixins/Trading.sol) で確認しています。

天気の価格は公式Data API v2の全ページの約定履歴です。売買気配や待ち数量ではないため、「判断後の最初の買い約定価格に1セントを加える」という仮定で損益を測ります。同時刻の即時約定、都合のよい将来の約定の選択、反対側価格の `1-p` による捏造は行いません。追加コスト2.5セント、5セントの場合も比較します。収益が正でも、この約定仮定の検証は別途必要です。

予報差戦略は、後半30日の全330市場・660 outcomeについて実際の板を取得し、追加評価しました。午前10時と60秒後の両時点で、選んだYes/Noそのもののaskに5株以上必要です。板の更新から60秒以内を主要条件とし、5秒・300秒も比較します。静かな板の継続性は仮定で、売り数量がない場合はnullとして扱います。元の予報校正と期待差を維持した [追加評価設定](research/weather_full_book_config.json) を保存しています。初回と同じ期間を再利用するため、利益が出ても未使用期間での確証とは呼びません。

## 再実行

作業場所はリポジトリルートです。既存のPython環境 `Polymarket_5m_btc/.venv` を使用しています。BTC板の読込にはpandas・pyarrow、公開アーカイブにはDuckDBとhttpfsが必要です。今回使用した版はpandas 2.3.3、pyarrow 21.0.0、DuckDB 1.5.6です。`.probe-deps` のDuckDBはこの研究ディレクトリだけに配置しています。

    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\setup_runtime.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\references\fetch_sources.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\btc_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\btc_backtest.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\expanded_book_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\btc_backtest.py --data polymarket-strategy-lab/data/btc_expanded_replay.csv --output polymarket-strategy-lab/results/btc_expanded --reference-source binance --oracle-mode spot_twap_proxy --max-book-snapshot-age 1
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_backtest.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_full_book_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_full_book_backtest.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe -m unittest discover -s polymarket-strategy-lab/tests -v
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\verify_expanded_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\audit_btc_expanded_results.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\audit_weather_full_results.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\verify_artifacts.py

データ取得は成功済みキャッシュを再利用します。期間や条件を変える場合は設定と保存先を分けてください。`data/` の生データは大きいためGitの追跡対象外ですが、取得URL、原本、部分データ、manifest、SHA256をローカルに保存します。追加研究の取得範囲を示す `data/weather_deep/metadata-manifest.json` と `books-manifest.json` はGitに保存します。生データがない新しいcheckoutでは、取得コマンドを実行してから成果物を検証してください。`.gitattributes` で改行変換を無効にし、SHA256照合に使うファイルのバイト列を保ちます。

バックテスト結果は `results/btc/`、`results/btc_expanded/`、`research/weather_results/`、`results/weather_full_books/` に出力します。`verify_artifacts.py` は必要な結果がない場合や入力と合わない場合に失敗します。`--allow-pending` は作業途中の確認用であり、完了の証拠には使いません。

観測NO戦略のゼロ取引を調べる補助診断は `research/weather_dead_bucket_diagnostic.py` と `research/weather_dead_bucket_book_diagnostic.py` です。同じPythonで実行できます。後者は既存の午前10時の板だけを利用し、終日の板評価や損益計算は行いません。

## 結果の読み方

取引ゼロは利益ゼロという実測評価ではなく、そのデータと条件では取引機会を確認できなかった結果です。統計的な信頼区間は日単位の依存を考慮して計算し、取引ごとの独立性は仮定しません。ドローダウンは決済時の資産残高から計算した値で、含み損や日中の流動性危機を含む最大損失ではありません。

BTCの遅延・コスト変更では約定する取引集合も変わります。そのため、同じ約定集合に追加コストだけを加える比較も保存します。遅延を増やした偶然の好成績を、耐性の証明として扱わないためです。採用候補、棄却、データ不足、約定仮定付きの成績を区別します。

## 天気予報差の追加研究

2026-10-05に、前回の利益集中・予報確率・執行条件を分解し、Chicagoの後続4日とNYC・Atlanta各34日を追加しました。固定条件の新しい72都市日では64仮想約定・+35.60954ドルでしたが、過去7日の気温平均との差の95%区間はゼロを含み、気象予報を使う追加価値は未確認です。詳細、データ欠損、費用・数量の感度、図、再実行コマンドは [天気予報差の追加研究](research/WEATHER_DEEP_REPORT.md) を参照してください。前回のChicago30日を新しい成績へ加えていません。
