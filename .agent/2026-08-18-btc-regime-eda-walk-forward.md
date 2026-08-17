# BTC先物レジーム研究品質EDAとwalk-forward検証 ExecPlan

このExecPlanは生きた文書である。`PLANS.md` に従い、`Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` を作業中に更新する。

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. 利用者は新しいテストコードを不要と明示したため、各段階はデータ検証・CLI実行・成果物監査で検証する。

## Goal

Binance `BTCUSDT` USDⓈ-M無期限先物について、価格・出来高・Funding Rate・未決済建玉系Metricsを統合し、七章の探索的データ解析、HMM/GMM/変化点検知、未来情報を使わないwalk-forward検証を実行して、日本語レポートと再実行コードを作り、最終的に`main`へpushする。

## Architecture

既存取得器を壊さず、追加データ取得、正規化、特徴量、EDA、レジーム、walk-forward、報告を小さなPythonモジュールに分離する。入力データはGit管理外、図表・要約・manifest・コードは追跡対象とする。分析は固定seedで一つのCLIから再現でき、失敗時は完成manifestを置換しない。

## Tech Stack

Python 3.13、pandas、NumPy、SciPy、Matplotlib、Seaborn、scikit-learn、hmmlearn、ruptures、statsmodelsを使う。ネットワーク取得には既存の標準ライブラリ実装を再利用し、環境プロキシを既定で無視する。

## Global Constraints

対象は`BTCUSDT` USDⓈ-M perpetualだけとし、現物・COIN-M・他銘柄を混ぜない。全時刻はUTCで保持し、JSTは季節性表示列としてだけ追加する。将来値を特徴量、標準化、状態命名に使わない。全期間fitは説明用、walk-forwardだけを時点整合した検証と呼ぶ。清算とCME価格は利用可能な公式データがないため代理と不足を明示する。新しいテストコードは作らない。

## Progress

- [x] (2026-08-18 00:00Z) 利用者が研究品質EDA、walk-forward、レポート、main pushを承認し、テストコード不要を指定した。
- [x] (2026-08-18 00:00Z) Binance Public Dataで月次Funding Rate、日次Metrics、対象期間の実在と列を確認した。
- [ ] 研究ブランチと依存環境を準備し、入力データ取得を実行する。
- [ ] 正規化・特徴量・七章EDA・レジーム・walk-forwardを実装する。
- [ ] 全分析を実行し、レポートと図表を生成・監査する。
- [ ] コードとレポートをmainへ統合し、remote mainを検証する。

## Surprises & Discoveries

- Observation: Binance Public DataのFunding Rateは月次のみ、Metricsは日次のみである。
  Evidence: `data/futures/um/monthly/` の一覧に`fundingRate`、`data/futures/um/daily/`の一覧に`metrics`があり、2025-08の実ファイルを取得して列を確認した。
- Observation: 公式アーカイブに`liquidationSnapshot`はなく、候補URLも404である。
  Evidence: 日次・月次トップレベル一覧に清算カテゴリがなく、2026-08-16の候補チェックサムが404を返した。

## Decision Log

- Decision: レジーム学習用の日足・1時間足を2020-01-01まで延長し、15分足とデリバティブ指標は直近1年を使う。
  Rationale: 日足365標本では3〜4状態の安定性評価が弱い一方、15分足の季節性とイベント分析は35,040標本で実行可能である。
  Date/Author: 2026-08-18 / Codex。
- Decision: 主レジームは1時間足、HMM/GMMは3・4状態、PELTは説明用とする。
  Rationale: 1時間足はノイズと標本数の妥協点で、PELTの全期間分割はオンライン信号ではない。
  Date/Author: 2026-08-18 / Codex。
- Decision: 新しいテストコードを作らず、manifest、データ品質検査、再実行、成果物照合で検証する。
  Rationale: 研究開発としての利用者の明示指示を優先しつつ、結果の再現性は落とさない。
  Date/Author: 2026-08-18 / Codexと利用者。

## Outcomes & Retrospective

未完了。最終的なデータ範囲、モデル選択、主要発見、限界、成果物、コミット、remote mainの状態を完了時に記録する。

## Context and Orientation

リポジトリルートは`C:\Users\Hodaka\Downloads\div\bot_research`である。対象サブプロジェクト`binance-btcusdt-futures-research/`には既存の`download_binance_klines.py`、直近365日の`data/BTCUSDT-{1d,1h,15m}-365d.csv`、公式ZIPを置く`raw/`、取得証跡`metadata/`がある。`data/`、`raw/`、`metadata/`は`.gitignore`で除外済みである。

今回追加する`src/btc_regime_eda/`は分析実装、`configs/research.json`は固定パラメータ、`collect_research_inputs.py`は追加取得、`run_research.py`は全工程CLI、`results/`は追跡する成果物である。分析実装は`data.py`、`features.py`、`descriptive.py`、`regimes.py`、`walk_forward.py`、`reporting.py`に分ける。

## Milestone 1: 環境と入力データを確定する

`binance-btcusdt-futures-research/pyproject.toml`へPython 3.13と分析依存を記録し、サブプロジェクト内`.venv`を作る。`configs/research.json`には開始日2020-01-01、分析終了日2026-08-17、直近詳細開始日2025-08-17、seed 42、状態数3・4、walk-forward学習730日、月次再学習、手数料0・5・10bpsを記録する。

`collect_research_inputs.py`は既存取得器の`fetch_archive`、チェックサム、ZIP読取を利用し、1時間足と日足を2020-01-01から2026-08-17まで取得する。Funding Rateは2025-08から取得可能な最終月まで、Metricsは2025-08-17から2026-08-16までの日次ZIPを取得する。正規化出力と`metadata/research-inputs.json`には各データの最初・最後の時刻、行数、欠損、重複、ソースURL、SHA-256を記録する。

作業ディレクトリをサブプロジェクトとして次を実行する。

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -e .
    .\.venv\Scripts\python.exe collect_research_inputs.py --config configs/research.json

受入条件は、1時間足と日足が2020-01-01から2026-08-16まで連続し、直近15分足が35,040行、Metricsが5分間隔で直近1年を覆い、Fundingの実際の終端がmanifestへ明示されることである。不足や404は黙って補間せず、データごとの`complete`または`insufficient_data`を記録する。

## Milestone 2: 特徴量と記述的EDAを作る

`src/btc_regime_eda/data.py`にCSV読込、UTC index、数値型、連続性、MTF as-of joinを実装する。`features.py`に対数収益率、realized volatility、ATR、モメンタム、出来高z-score、Taker imbalance、効率比、Hurst、RSI、日足SMA20傾きを実装する。ローリング窓の先頭欠損は削除し、未来埋めはしない。

`descriptive.py`は七章のうち分布、季節性、ローソク足、出来高、MTF、Triple Barrier・リスク、デリバティブevent studyをそれぞれ関数として提供する。ブレイクアウトは直前20本の高安を更新した足、ダマシはその足の終値が直前レンジ内へ戻った場合と定義する。inside/outside bar、長ヒゲ、3倍出来高、上下3%足は全て事前に固定した定義をmanifestへ書く。

`run_research.py --stage descriptive`を実行し、各表の件数が0でないこと、図が開けるPNGであること、主要表に件数・平均・中央値・95% block-bootstrap区間があることを確認する。30件未満のセルは`insufficient_data`にする。

## Milestone 3: HMM・GMM・変化点を比較する

`regimes.py`にRobustScaler、GMM、Gaussian HMM、PELTを実装する。全期間モデルは説明用として2020-01-01以降をfitし、3・4状態とseed 42・43・44・45・46を比較する。GMMはBIC、HMMは時系列分割した末尾20%の対数尤度、全モデルは最小占有率5%、平均継続時間、seed間ARIを集計する。

状態名は学習期間内の平均収益率・volatility・momentumだけから決める。最大volatilityかつ負の収益を`bear_stress`、正のmomentumと正の収益を`bull_trend`、最小volatilityを`quiet_range`、残りを`volatile_transition`とする。定義が衝突した場合は優先順を固定し、レポートに状態統計を載せる。

PELTは標準化した収益率とrealized volatilityに適用し、penalty感度を3値で表示する。変化点は将来を見た事後分割であり、walk-forwardシグナルには使用しない。

## Milestone 4: 時点整合walk-forwardを実行する

`walk_forward.py`は2022-01-01以降、過去730日だけで毎月再学習し、翌月を推定する。GMMは`predict_proba`、HMMは学習済み初期確率・遷移行列・ガウス密度から独自forward recursionを使い、テスト月の未来観測を使う平滑化を禁止する。各再学習で状態名も学習窓だけから決める。

評価表にはレジーム占有率、遷移率、次1時間・24時間収益、次24時間volatility、最大逆行幅、月間状態安定性を含める。診断戦略はSMA24/168順張りと24時間z-score逆張りで、無条件、レジームゲート、buy-and-holdを0・5・10bpsで比較する。状態ゲートは学習窓内で各戦略の平均が正だった状態だけを翌月有効化し、テスト月結果による再選択をしない。

受入条件は、すべてのwalk-forward行に`model_fit_end < prediction_time`が成立し、未来参照監査が0件、全期間説明用結果とwalk-forward結果が別ファイルであること、手数料感度で結論が反転する場合にレポートがそれを明記することである。

## Milestone 5: レポートを生成し監査する

`reporting.py`は`results/report.md`、`results/manifest.json`、`results/figures/*.png`、`results/tables/*.csv`を生成する。レポートは要約、データ品質、七章EDA、レジーム比較、walk-forward、限界、再実行方法を含む。主要結論は表の実測値から生成し、テンプレートの固定文で有意性を断定しない。

次を実行する。

    .\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage all
    .\.venv\Scripts\python.exe run_research.py --config configs/research.json --stage verify

検証は、CSV時刻・行数・重複、walk-forward未来参照、NaN/inf、全成果物の存在、PNGデコード、Markdown内リンク、表と本文数値、固定seed再実行の要約ハッシュを確認する。レポートを目視し、図の軸、凡例、日本語文字化け、極端な外れ値で読めない図を修正する。

## Milestone 6: Gitとmainへ統合する

`.gitignore`へ`.venv/`、追加raw・data・metadata、作業キャッシュを追加し、`results/`は追跡対象に残す。`git status -sb`、`git diff --check`、大容量ファイル監査、秘密情報検索を行い、明示パスだけをコミットする。

研究ブランチをpush後、ローカル`main`を`origin/main`へfast-forwardし、研究ブランチを`--no-ff`でmergeする。競合があれば停止して内容を確認し、force pushはしない。mainで最終verifyを再実行してから`git push origin main`を実行する。最後に`git status -sb`、`git log origin/main..main`がクリーンで、GitHub上の`origin/main`が最終コミットを指すことを確認する。

## Validation and Acceptance

完了は、(1)公式チェックサム付き入力manifest、(2)七章の図表、(3)HMM/GMM/PELT比較、(4)時点整合walk-forward監査0件、(5)日本語レポート、(6)再実行可能コード、(7)remote main反映の七項目がすべて現在のファイルとコマンド出力で証明された場合だけ宣言する。利益が出ない、状態が不安定、追加データが不足する結果も正しい研究結果として報告し、成功へ見せかけない。

## Idempotence and Recovery

取得済みZIPはチェックサム一致時に再利用する。正規化CSV、表、図、manifestは一時ファイルから置換し、中断時の部分成果物を完成扱いしない。モデルseedと設定は固定する。ネットワーク障害は同じ取得コマンドで再開でき、404は対象データの不足として記録する。Git統合前は研究ブランチに全成果物を保持し、main push失敗時もforce pushせず原因を調べる。

## Interfaces and Dependencies

`collect_research_inputs.py`は`--config`を受け、`metadata/research-inputs.json`を返す。`run_research.py`は`--config`と`--stage {descriptive,regimes,walk-forward,all,verify}`を受ける。`data.py`は`load_klines(interval) -> DataFrame`、`features.py`は`build_hourly_features(frame) -> DataFrame`、`regimes.py`は`fit_regime_models(features, config) -> RegimeResult`、`walk_forward.py`は`run_walk_forward(features, config) -> DataFrame`、`reporting.py`は`build_report(results, output_dir) -> Path`を提供する。

## Change Note

2026-08-18: 利用者が研究品質EDA、walk-forward、レポート、main push、新規テスト不要を指定したため、データ取得からGit統合までを一つの自己完結した計画として作成した。
