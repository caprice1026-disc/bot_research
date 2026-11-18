# 2026-10-31 論文検証とBinanceリプレイ

結論は [REPORT.md](REPORT.md) を参照。3論文の一次資料照合、公式RL保存結果の再計算、独自の報酬・集中リスク・復旧実験を行う。ライブ発注経路ではない。

## 改訂記録（2026-11-18）

保存済み10月レポートはコミット `6db1f50` の会計で作られた。現行版は Funding 後、任意注文より先に既存建玉の始値 mark で維持証拠金を確認し、清算時の残高ゼロ下限に伴う不足金を別記する。過去の表は上書きしていない。[既存48条件と新旧結果の比較](../../trading-core/validation/EXECUTION_BOUNDARIES.md) を参照。旧結果を完全再現するには `6db1f50` の別作業ツリーを使用すること。

## 実行

リポジトリルートからPowerShellで実行する。使用したPythonは3.10.9。数値ライブラリはNumPy 1.23.5、Pandas 2.0.3、SciPy 1.11.1、Numba 0.57.1、Matplotlib 3.7.1。画像検証にPillow、テストにpytest 8.3.5も使う。

```powershell
$studyPython = 'C:/Users/81905/AppData/Local/Programs/Python/Python310/python.exe'
$studyRoot = 'binance-btcusdt-futures-research/paper_validation_20261031'
& $studyPython -X utf8 -B "$studyRoot/run_study.py" --stage collect
& $studyPython -X utf8 -B "$studyRoot/run_study.py" --stage run
& $studyPython -X utf8 -B "$studyRoot/run_study.py" --stage verify
& $studyPython -X utf8 -B "$studyRoot/supplement/run_robustness.py"
& $studyPython -X utf8 -B "$studyRoot/supplement/leverage_stress.py"
& $studyPython -X utf8 -B "$studyRoot/test_study.py"
```

`collect`は公開ZIPを取得し、既存のchecksum一致済みファイルを再利用する。月次mark価格の欠損日は公式日次ZIPで照合する。取得失敗をFundingゼロや価格補間へ置き換えない。`run`は価格・設定・コードのハッシュが同じ成功結果を再利用し、`verify`は成果物ハッシュと境界条件を検査する。追加検査も同一入力の成功結果を再利用する。

生データ、正規化NPZ、学習済み方策、目標建玉経路、取得した外部実装は、Git管理外の `binance-btcusdt-futures-research/data/paper_validation_20261031/` へ保存する。公開結果はこのディレクトリの `results/` に残す。

## 公式実装の再計算

```powershell
& $studyPython -X utf8 -B "$studyRoot/run_study.py" --stage upstream
$upstreamRoot = Join-Path (Get-Location) 'binance-btcusdt-futures-research/data/paper_validation_20261031/upstream/Reward-alignment-9e5b2aaf14123e8f960fd304e46bfefa519dafac'
$env:PYTHONPATH = $upstreamRoot
$env:MPLCONFIGDIR = Join-Path (Get-Location) 'binance-btcusdt-futures-research/data/paper_validation_20261031/matplotlib'
& $studyPython -X utf8 -B "$upstreamRoot/scripts/check_outputs.py"
& $studyPython -X utf8 -B "$upstreamRoot/scripts/finalise_rl_results.py"
```

上流のコマンドは、取得したコピーの中に再計算結果を書き出す。保存済み方策の再計算であり再学習ではない。今回のstdoutを `results/upstream_reconstruction_stdout.json` に保存してある。revisionとアーカイブハッシュは `results/upstream_provenance.json` に記録。

FIDES公開sourceの一覧確認は `supplement/fetch_fides_source.py`。取得URLとファイル一覧は `results/fides_source_manifest.json` に残る。

## テスト

新規テストだけなら上記`test_study.py`を標準ライブラリのunittestで実行できる。既存テストも含めた今回のコマンドは次のとおり。`test_deps`はpytestを隔離して導入した場所であり、Python環境にpytestがある場合はその指定を外せる。

```powershell
$env:PYTHONPATH = "$(Get-Location)/binance-btcusdt-futures-research/data/paper_validation_20261031/test_deps;$(Get-Location)/binance-btcusdt-futures-research/paper_validation_20261031"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
& $studyPython -X utf8 -B -m pytest binance-btcusdt-futures-research/tests "$studyRoot/test_study.py" -q -p no:cacheprovider
```

今回の結果は `21 passed`。通常の依存関係は `requirements-study.txt` に列挙した。

## 解釈上の区分

`engine.py`のRLは表形式の独自実験で、元論文のactor–criticではない。`operational_audit.py`はローカル模擬取引所で、Binance/Hyperliquidの注文APIを検証しない。集中度実験はルール群で、LLM通信実験ではない。`supplement/`は主結果を見た後の追加感度であり、独立した最終確認ではない。

スリッページは固定仮定、注文数量は連続値、Funding決済markは1分始値近似、清算は固定維持証拠金率の代理判定。従って成績表と実取引所での収益・清算回避は同一視しない。
