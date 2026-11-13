# AIトレーダー3論文の照合とBinance過去データ検証

このExecPlanはリポジトリルートの `PLANS.md` に従う。Progress、Surprises & Discoveries、Decision Log、Outcomes & Retrospectiveを作業に合わせて更新する。

## Purpose / Big Picture


2026年10月23〜30日の週報にあるFIDES-Live、Reward Alignment、Trading Agent Swarmsについて、論文が実際に示す内容と、BTCUSDT無期限先物で役立つ内容を区別する。一次資料の照合、可能な公式結果の再計算、Binance公開データによる時系列バックテスト、注文記録の故障注入を行い、再実行できるコードと日本語レポートを残す。論文の主張を再現した結果と、論文に着想を得た独自実験を混同しない。

## Progress


- [x] (2026-10-31 JST) 既存README、データ取得器、研究runner、PLANS.md、作業差分を確認した。
- [x] (2026-10-31 JST) 3論文のarXiv本文とReward Alignment公式GitHubを確認した。
- [x] (2026-10-31 JST) 公式ZIP37本をchecksum検証し、価格・mark各525,600本、Funding1,095回の連続性を確認した。
- [x] (2026-10-31 JST) 公式RL保存方策1,660件を再計算。最大差9.87e-14、証明上界違反0。FIDES公開source内の表とSwarms本文も照合した。
- [x] (2026-10-31 JST) 報酬・集中リスク・監査の独自実験を実装。既存10件＋新規11件のテスト、計21件通過。
- [x] (2026-10-31 JST) 主実験63月次fit・48条件、事後の低頻度/遅延感度25条件、レバレッジ感度15条件を実行。300故障ケースの2方式比較と日本語報告を保存した。

## Surprises & Discoveries


READMEには過去のBinance研究とLLM pilotの記録があるが、現在の作業コピーには価格CSV、Funding CSV、保存済みLLM回答、各プロジェクトの仮想環境がない。既存結果を実行済みとみなさず、必要な公開データを取得する。Python 3.10にはNumPy、Pandas、SciPy、Matplotlib、PyTorch、Numbaがあり、pytestは未導入。通常sandboxからのネットワークはWinError 10013で制限される。

月次mark価格の2026年6月アーカイブはchecksum一致でも6月29日を欠いていた。公式日次アーカイブを追加取得し、補間なしで1日分を回復した。Funding実時刻は8時間境界から最大26msずれており、決済markは1分始値近似とした。

FIDESの公開arXiv sourceには集計表・画像・TeXだけが含まれ、本文が言及する正規化監査データや分析コードは見つからなかった。したがって独立再現範囲は表の算術に限定した。元論文の62.63%はラウンド単位ではなく評価可能な個々の制約単位の割合である。

粗利益だけを学ぶRLと短期ルールは費用で資金のほぼ全額を失った。リスク係数10は+0.21%だが、活動月は2か月だけで、スリッページを1bpから3bpへ変えると-0.42%になった。係数50などのゼロ取引を収益評価の成功とはしていない。

## Decision Log


2026-10-31: 新規研究は `binance-btcusdt-futures-research/paper_validation_20261031/` に隔離する。既存のライブ注文処理や他研究の未コミット差分には触れない。公開データ取得に既存のchecksum解析を再利用する。既存の全データ・Metrics収集runnerは今回不要なデータも取得するため、必要な価格・mark価格・Fundingだけを取得する研究runnerを追加する。

2026-10-31: 対象週は論文の公開期間として維持する。バックテスト期間は別に明示し、直近の完全な12か月（2025-10-01〜2026-10-01 UTC、終端除外）を第一候補とする。最初の3か月を初期学習、その後の9か月を月次の未見評価にする。公開欠損があれば埋めずに記録し、取得できた連続期間と主評価への影響を明記する。パラメータを評価成績で選ばない。

2026-10-31: LLMの回答・通信を固定ルールで代用してLLM成績とは呼ばない。保存済み回答がないため、LLM単独・LLM+RLの優劣は今回の市場リプレイでは判定しない。集中リスクは独立した単純戦略を共通シグナルへ混合する機構実験として扱う。監査はローカル模擬取引所での実装検証として扱い、実取引所への保証に拡大しない。

2026-10-31: 主実験を見た後で、低頻度の単純ルールと静的な半分サイズの対照を追加した。短期ルールの費用損失だけで集中制限の有効性を判断しないためである。これらは `supplement/` の事後感度として明示し、元の主結果を保存した。レバレッジは学習済み方策の外挿であり、清算の実運用評価とは呼ばない。

## Outcomes & Retrospective


`binance-btcusdt-futures-research/paper_validation_20261031/REPORT.md` に結論を保存した。公式RL結果の再計算に成功し、Binanceの指定した1年分のOHLC/mark/Fundingが揃った。独自バックテストでは頑健な収益優位性を確認できず、約定記録照合と費用・活動率・集中度の監査に有用性を認めた。模擬復旧300ケースでは意図しない建玉とTP/SL欠落が0だったが、取引所APIの競合や復旧時間は未検証。板データ、実LLM通信、実取引所の清算tierを再現していない点を報告に残した。

## Context and Orientation


`binance-btcusdt-futures-research/download_binance_klines.py` はBinance公式ZIPとCHECKSUMのSHA-256一致を検証する。`src/btc_regime_eda/collection.py` はFundingの月次アーカイブに対応する。`hyperliquid-ai-trader/src/hyperliquid_ai_trader/research/` は独立した短期取引の評価器であり、今回の継続ポジションと月次学習をそのまま扱うものではない。既存の取引基盤を改造せず、研究専用runnerで実験する。ウォークフォワードとは、各評価月より前だけで学習し、その月の結果を未来として再生する方法を指す。

## Plan of Work


まず一次資料のURLと公式実装のrevisionを記録する。原データ非公開の研究は統計の独立再計算ができない旨を明記する。公式Reward Alignment実装は別のローカル写しに取得して内容を確認し、既存の保存済み方策を再計算する。全学習の不要な再実行はしない。

次に研究専用設定、公開データ取得器、データ監査を用意する。確定1分足から5分判断の特徴量を構成し、注文は判断後の分の価格で処理する。Fundingは決済時刻に保有している建玉へ課金し、欠損をゼロ扱いしない。板履歴がない場合、スリッページは複数水準の仮定として明示する。清算はmark価格と明示した維持証拠金の近似として扱い、実際の段階別証拠金や注文板を再現したとはしない。

報酬比較は同じ状態、行動集合、学習期間、評価期間でコストとリスク項の有無を変える。現金と固定ルールを基準にする。集中リスク比較は共通信号の混合率とポジション制限を変え、独立基準からの相関・同時決済・純損益・ドローダウンの変化を測る。監査比較は同一注文ID、約定ID、再起動時の状態照合を使い、発注後・部分約定後・保護注文登録途中の故障を注入する。すべて独自実験であることを結果ファイルにも記す。

## Concrete Steps


作業ディレクトリはリポジトリルート。Pythonは以下を用いる。

    & 'C:/Users/81905/AppData/Local/Programs/Python/Python310/python.exe' -X utf8 -B binance-btcusdt-futures-research/paper_validation_20261031/run_study.py --stage collect
    & 'C:/Users/81905/AppData/Local/Programs/Python/Python310/python.exe' -X utf8 -B binance-btcusdt-futures-research/paper_validation_20261031/run_study.py --stage run
    & 'C:/Users/81905/AppData/Local/Programs/Python/Python310/python.exe' -X utf8 -B binance-btcusdt-futures-research/paper_validation_20261031/run_study.py --stage verify

追加感度は `supplement/run_robustness.py` と `supplement/leverage_stress.py`。全成果物・rawハッシュを再確認するコマンドは次のとおり。

    & 'C:/Users/81905/AppData/Local/Programs/Python/Python310/python.exe' -X utf8 -B binance-btcusdt-futures-research/paper_validation_20261031/supplement/final_verify.py

新規重要挙動11件は同じPythonで `paper_validation_20261031/test_study.py` を実行する。既存テストを含む21件は、研究用 `data/paper_validation_20261031/test_deps` のpytestをPYTHONPATHへ指定し、`python -m pytest binance-btcusdt-futures-research/tests binance-btcusdt-futures-research/paper_validation_20261031/test_study.py -q -p no:cacheprovider` で通過した。具体的な環境変数設定は研究ディレクトリのREADME.mdに記載した。

## Validation and Acceptance


価格とFundingのchecksum、期間、重複、欠損を報告する。未来の足を変えてもそれ以前の判断が変わらないこと、往復取引の費用、Fundingの符号と境界、故障後の重複約定や保護注文欠落がないことをテストする。全方式で同じ月を使い、評価期間の成績で学習・選択しない。純損益とMDDに加え回転率、月別成績、日次ブロック再標本化による差の不確実性を保存する。ゼロ取引の場合は収益性を評価できたとは記さない。

## Idempotence and Recovery


公開ZIPはchecksum一致済みなら再取得しない。取得失敗をmanifestへ残し、再試行では未取得分だけを扱う。学習済み実験はコード・設定・データのハッシュが同じ場合だけ再利用する。生成物は今回の研究ディレクトリへ保存し、元原稿と既存成果物を上書きしない。

## Artifacts and Notes


一次資料は https://arxiv.org/html/2610.36476v1 、https://arxiv.org/html/2610.34153v2 、https://arxiv.org/html/2610.37448v1 。公式実装は https://github.com/DataismLab/Reward-alignment 。未検証の著者数値を独立再現済みと呼ばない。

## Interfaces and Dependencies


研究runnerはPython 3.10、NumPy、Pandas、SciPy、Matplotlibを使う。ネットワーク処理はcollect段階に限定する。runとverifyはローカルデータだけで動作する。実注文、秘密鍵、外部へのメッセージ送信を必要としない。
