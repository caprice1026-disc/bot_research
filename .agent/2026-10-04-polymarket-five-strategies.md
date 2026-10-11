# 現行Polymarket向けの戦略作成と実データ検証

このExecPlanはルートの `PLANS.md` に従う。進捗、発見、判断、結果は作業中に更新する。

## Purpose / Big Picture

ユーザー指定の prediction-market-backtesting、CloddsBot、polybot、PolyWeather を利用し、Polymarket用Botの戦略を5つ程度に絞って実装し、実データのバックテストで採否を判断できるようにする。CloddsBotは依頼文で重複しており、対象は4リポジトリである。成果物は `polymarket-strategy-lab/` にまとめる。現在の手数料、決済ルール、実行可能価格を反映し、収益が確認できない戦略を成功と扱わない。

依頼の完了には戦略の具体的な売買条件、再実行可能なコード、実行済みバックテスト、データの期間・欠損、コスト感度、採否と限界を記した日本語レポートが必要である。実注文の発注や公開は依頼に含まれない。

## Progress

- [x] (2026-10-04) ルート規約、既存作業状態、goalを確認。既存の未追跡bonereaper研究は別作業として保存する。
- [x] (2026-10-04) 指定4リポジトリのWebページと公式手数料資料を取得。ソース取得、手元データ、現行APIの調査を独立して開始。
- [x] (2026-10-04) 4リポジトリの全2,206ファイルとSHAを固定。references/SOURCE_AUDIT.mdに設計対応とライセンスを記録。
- [x] (2026-10-04) 既存BTC SQLite 5,251,675イベントを受信順に検査し、25市場7,198秒の再生CSVを生成。原本・CSVのSHA256確認済み。
- [x] (2026-10-04) BTC 3戦略を実装し、既存記録と拡張30日各1時間・360市場の実データで実行。全377 scenario約定を独立計算で照合。
- [x] (2026-10-04) 拡張BTCは107,849行、Binance実1秒足111,600秒・欠損0。Chicago天気は60日・660市場・93,852約定、取得エラー0。
- [x] (2026-10-04) 5戦略を実装。天気の初回評価は後半30日に予報差1約定、観測NOは0試行。現在の5shares下限はPlace Ordersの株数仕様に基づく。
- [x] (2026-10-04) BTC後半15日・コスト感度・block bootstrapを再計算。確率差−21.57024、優勢側−5.16474、完全セットは機会なし。固定条件で採用を支持しない。
- [x] (2026-10-04) 天気予報差を全30日・330市場・660 outcomeの実板で追加評価。主条件27約定・+17.95562、鮮度5秒では9約定・−2.13668。既見期間の探索的追試として区別した。
- [x] (2026-10-04) 天気9条件の5,940候補・159約定、795入力hash、校正・会計・bootstrapを独立監査。観測NOの0取引も段階別と10時の実板で診断した。
- [x] (2026-10-04) 最終47テスト成功、通常モード成果物検証は全5群passed。REPORT.mdとREADME.mdの数値・ローカル参照を照合し、5戦略の実装・実行・採否・制約・再実行手順を揃えた。

## Surprises & Discoveries

現行公式資料 `https://docs.polymarket.com/trading/fees` は手数料を株数C、価格p、料率rについて `C*r*p*(1-p)` と説明する。cryptoのrは0.07、weatherは0.05。旧リポジトリの固定料率や旧曲線をそのまま採用できない。市場別メタデータと資料の取得時点を記録する。

手元にはBTC 5分市場の2時間収集アーカイブと天気研究がある。前者は5,251,675イベント・partial終了で、受信順と欠損を再検査した。後者の予報・観測・価格に期間不一致があるため、そのまま収益評価に使わず、60日分を新たに取得した。

既存BTCは5,392秒で両側の板が有効。配信最良値と復元板の不一致20,422件を棄却した。完全セットの同時ask合計最小値は1.01であり、即時買いの裁定機会は確認できない。最終実装の通常条件では確率差9取引が+1.81109、優勢側5取引が-3.88801ドル。初回の61観測TWAPを正しい60観測へ修正して再計算した結果である。1日分なので収益性の確証とは扱わない。

従来CLOBの終了済み市場prices-historyは空だが、公式Data API v2のtrades、prices-history、resolutionsで過去データを取得できる。cursor次ページにもconditionを保持する必要があり、100+100+27件と一括227件の一致を検証した。BTCの公開ParquetはDuckDBのHTTP range読込で必要市場だけを抽出できる。

現行BTCの公式決済文面はChainlink 60秒TWAPを参照する。モデルを最終60秒平均の確率推定へ修正した。拡張データはBinanceの実1秒足から作る代理変数を使用し、記録されたChainlinkと混同しない。Chicagoの決済元は2026-08-23以降NOAAに変化しており、市場文面を日別に保存する。

天気の次の買い約定がないことはaskがないことを意味しない。初回20試行の実板監査では、60秒以内の更新を許す条件で15試行に購入可能数量が見つかった。この部分集合を収益評価へ読み替えず、全30日の全区間で予報差を追加評価する。静かな板を一律5秒途絶で無効にするBTC条件も天気には移さない。one-sidedの実askを扱う処理はopt-inで追加し、BTC既定動作を維持した。

## Decision Log

- Decision: 新しい研究ディレクトリを追加し、既存研究データは読み取りで利用する。
  Rationale: 他の未完了作業と原データを保存し、依頼された成果物を独立して再現する。
  Date/Author: 2026-10-04 / Codex
- Decision: 戦略と閾値はデータの利用可能性を確認して具体化し、評価用期間を見る前に設定を記録する。
  Rationale: 勝てる設定だけを事後選択することを避け、実際に実行可能な戦略を検証する。
  Date/Author: 2026-10-04 / Codex
- Decision: BTC拡張は2026-09-01〜09-30の16:00〜17:00 UTCを毎日採用。天気はChicago KORDの2026-08-01〜09-29、前30日校正・後30日評価。
  Rationale: 結果を見ずに期間・時刻を固定し、短い既存記録と独立した後続期間を検証する。全日連続観測との違いを明示する。
  Date/Author: 2026-10-04 / Codex
- Decision: BTCは遅延・コストを増やす再生と、同じ約定集合への追加価格差の両方を保存。
  Rationale: 遅延によって負け取引だけが偶然約定しなくなる選択効果を、コスト耐性と誤認しない。
  Date/Author: 2026-10-04 / Codex
- Decision: 天気追加板の主要鮮度条件60秒、感度5秒/300秒を全cohort損益の計算前に記録。原型の予報fit、8ポイント差、5株、60秒遅延を維持する。
  Rationale: 特定の有望な20試行だけの選択バイアスを避け、板と履歴価格の違いを検証する。既に見た期間なので探索的な結果と明示する。
  Date/Author: 2026-10-04 / Codex

## Outcomes & Retrospective

依頼された5戦略の実装、実データの実行、コスト感度、時間分離、品質検査、採否根拠、日本語レポートと再実行手順が揃った。BTC確率差は後半15日に−21.57024、優勢側は−5.16474、完全セットは機会なし。天気予報差の全板追試は+17.95562で研究候補だが、95%区間が0を含み、鮮度条件に依存し、未使用期間の確証でもない。観測NOの0取引は、温度条件が起きないためではなく、履歴価格の不足・高値、追加10時診断ではNOのaskなしによるものだった。

実装の正常性と収益性の証拠を分け、5候補のうち現在の実運用を支持する優位性を確認できたものはないと結論した。利益のある結果だけに閾値を合わせず、負の結果と不足も成果物に保存した。実注文・公開・pushは依頼範囲ではなく実施していない。Codex利用上限リセットは未使用。

## Context and Orientation

作業ディレクトリは `C:\Users\Hodaka\Downloads\div\bot_research`。`Polymarket_5m_btc/` は公開板・取引所データ収集、`weather-polymarket-research/` は天気予報の時点整合検証を持つ。`polymarket-bonereaper-research/` は別件の未追跡作業で、変更しない。Pythonは既存の `Polymarket_5m_btc/.venv/Scripts/python.exe` または天気研究の仮想環境を利用する。

板は買値・売値と各価格で取引できる数量を表す。履歴価格だけでは板の数量、注文の待ち順、両側同時約定は分からない。この違いをデータごとに明示する。時点整合とは、判断時刻までに公開・受信された情報だけでシグナルを計算すること。時間外検証は、設定に使わない後続期間の成績を別集計すること。

## Plan of Work

第一段階では `polymarket-strategy-lab/references/` に指定リポジトリのソースとコミット情報を保存する。`research/` に現行仕様と既存データの調査を記録する。ライセンスを保存し、利用する関数や設計と新実装の対応を説明する。

第二段階では公開APIまたは公開アーカイブから検証用データを取得する。生データと取得時刻、リクエスト、SHA256、成功・欠損一覧を `data/` に保存する。成功済みの取得は再利用し、未処理・失敗分を再取得する。現行APIで提供されない履歴は捏造せず、他の実データ経路を調べる。

第三段階で戦略の条件、手数料、スリッページ、取引量上限、重複エントリー防止、決済、資金制約を実装する。共通の薄いPython処理を優先し、必要以上のフレームワークを増やさない。板がない場合は仮定付き価格リプレイとして明示し、約定が証明された結果とは区別する。

第四段階でバックテストを実行する。期間別、戦略別、コスト別に取引数、純損益、投入資金、最大ドローダウン、欠損による棄却件数を残す。結果が悪い戦略も記録する。レポートの数値と機械可読結果を照合する。

## Concrete Steps

リポジトリルートで `git status --short` を確認し、他の変更を保存する。利用環境は次の実行形式を使う。

    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\btc_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\btc_backtest.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\expanded_book_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\btc_backtest.py --data polymarket-strategy-lab/data/btc_expanded_replay.csv --output polymarket-strategy-lab/results/btc_expanded --reference-source binance --oracle-mode spot_twap_proxy --max-book-snapshot-age 1
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_backtest.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_full_book_data.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\weather_full_book_backtest.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe -m unittest discover -s polymarket-strategy-lab/tests -v
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\audit_btc_expanded_results.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\audit_weather_full_results.py
    .\Polymarket_5m_btc\.venv\Scripts\python.exe polymarket-strategy-lab\verify_artifacts.py

各コマンドはmanifestまたは結果JSONを出力する。BTCの既存取得を再実行すると12.1GBを読み直すため、設定変更がなければ生成済みCSVとmanifestを再利用する。拡張・天気取得は成功済みキャッシュを利用し、未処理分を取得する。

## Validation and Acceptance

正常性検証では未来の入力の混入、価格範囲、時刻の整列、両側の非同期、数量超過、資金不足、手数料の計算、決済ラベルの扱いを確認する。研究の受入条件は約5戦略について実データの実行結果と採否根拠が揃うこと。短期間、ゼロ取引、価格だけの約定仮定は限界として明記し、十分な収益性の根拠へ読み替えない。成功戦略の数を先に保証しない。

## Idempotence and Recovery

原データは上書きせず、取得条件ごとの保存先を使用する。不確実な外部書込みは行わない。中断時は既存成果物と実プロセスを確認し、動いている取得を重複起動しない。API制限やデータ欠落はmanifestに残し、独立して進められる検証を続ける。

## Artifacts and Notes

ソースmanifest、データmanifest、戦略設定、取引明細、集計、検証ログ、日本語レポートを成果物とする。利用上限リセットはユーザーが1回まで許可している。必要になるまで使用しない。

## Interfaces and Dependencies

公開HTTP取得とJSON/CSV/SQLiteは標準ライブラリを優先する。大量Parquetが必要なら既存環境のduckdb/pyarrowを確認する。戦略関数は過去データと固定設定から注文候補を返し、損益計算と分離する。データと結果に `status` を持ち、`insufficient_data`、`rejected`、`inconclusive`、検証に裏付けられた候補を区別する。

更新記録: 2026-10-04 初版。依頼の範囲と完了条件を固定し、実データの調査結果に応じて実行箇所を具体化する。

更新記録: 2026-10-04 ソース固定、既存BTC実行結果、公式v2の履歴取得、TWAP・天気決済ルールの発見、拡張期間と再実行コマンドを追記した。初回結果を完成と読み替えず、残る評価をProgressに明示した。

更新記録: 2026-10-04 拡張BTCと天気60日の実行完了・独立監査を反映。TWAP端点修正後の数値に更新。約定履歴の制約を受けた天気全板の探索的追試と残作業を明記した。

更新記録: 2026-10-04 天気全板・0取引診断・独立監査・47テスト・通常成果物検証・日本語レポートを完了。採用根拠が未成立という研究結論を明記し、要求された検証の完了と実運用の成功を分離した。
