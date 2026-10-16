# 価格だけを使う決済方策比較

固定entryのBTC/ETH現物相当long-onlyに、固定決済、MLP、ゼロからのActor–Critic、MLP初期化Actor–Criticを適用する独立した研究実装。外部サービス呼出し・取引所注文機能はない。

原典は [MLP-Initialized Reinforcement Learning for Exit Timing in SPX Short-Straddle Portfolios](https://arxiv.org/html/2610.13126v1)、公式repoは [spx-short-straddle-rl](https://github.com/Justinnnn0313/spx-short-straddle-rl)。SPX option quote、Greeks、VRP、margin、settlementを再現していない。暗号資産価格データへの適応であり、論文の完全再現・論文性能の検証とは呼ばない。

## 実行

repo rootのPowerShellでPython>=3.10とrequirements.txtの依存（NumPy、pandas、PyTorch CPU、psutil、図用matplotlib）を用意する。例: `python -m pip install -r exit-policy-research/requirements.txt`。入力は隣の `swingguard-research/data/BTCUSDT-5m.csv` と `ETHUSDT-5m.csv` を読み取るだけで、変更しない。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s exit-policy-research/tests -v
python -u exit-policy-research/research.py --config exit-policy-research/config.json --output exit-policy-research/results/reproduce-v1
python -u exit-policy-research/verify_results.py --output exit-policy-research/results/reproduce-v1 --evidence exit-policy-research/results/reproduce-v1.verification.json
python exit-policy-research/build_report.py --output exit-policy-research/results/reproduce-v1
```

outputが存在すると停止するため、再実行には未使用名を指定する。configを変更して再実行すると別実験である。結果の良いseed・threshold・checkpointを後から選ばない。CPUは1thread、process memoryはWindows Job Objectで2048MiBを上限にする。実測環境・ピークメモリはruntime.jsonに記録する。

## 固定条件

BTC/ETH各50%の独立資金slotに分け、過去データ7日warmup後から49時間ごとに両銘柄を買う。entry時刻はexit判断に依存せず、早期決済後の資金は次の予定entryまで現金で待機する。slot内は複利、銘柄間の資金移動なし、借入・空売りなし。初期資産100,000 USDT、買付数量は手数料込みの資金制約を満たす。固定entryは予測優位を持つ戦略ではなく、決済比較の共通実験条件である。

最大48時間、裁量決済の最低2時間、1時間ごとの確定5分足終値で判断する。24時間市場向けに置いた適応パラメータである。SL4%・TP8%は買付fill基準で全方策共通。5分足で保護注文を処理し、最低保有の前でも作動する。裁量売却は判断後の最初の5分始値、最大保有は48時間後の始値。gap stopは悪化した始値、gap TPはtarget価格に抑える。満了openでもgap SL/TPを優先し、満了足の後続high/lowは参照しない。通常の売却openは同じ足のintrabar到達より先、同足SL/TPはSL優先。intrabar時刻は5分内で不明と保存する。

片道fee10bp・slip5bp。板・出来高制約・市場インパクト・動的spread・注文最小単位はモデル化しない。stressではfee/slipを両方2倍、または裁量決済を5分遅延し、保護注文を継続する。予定買付は時刻固定のmarket-on-openとして追加遅延対象に含めない。stressで再学習は行わない。

固定ルールは共通SL/TPの下で48時間まで保有する `fixed_max` と、12時間で決済する `fixed_12h` の2種類。MLP・raw RL・MLP→RLはexit確率0.5を固定thresholdにする。

## 情報分離と学習

利用可能期間からwarmup7日を引いた時刻を60/20/20に事前分割する。episodeのentryから最大保有の終端openまで全経路が1区間内に入る場合だけ採用し、境界を跨ぐepisodeをpurgeする。過去の特徴計算用履歴は使用できるが、val/testの状態・ラベル・報酬を訓練更新に使わない。

14特徴は1h/6h/24h/7dの過去return、24h vol、1h range、24h平均に対する1h volume、UTC時刻sin/cos、保有時間、未実現return、closeベースMFE/MAE・peakからの下落。各特徴の使用時刻は `available_ms`。データ全体からrollingを計算しても未来側を使わない。scalerはtrainの反実仮想hold経路の状態だけでfitし、各入力を±20にclipする。

oracleはtrain episode内の実行可能な決済候補を共通費用・保護注文込みで評価し、netが最大になる最初の候補を選ぶ。その判断以前をhold、それ以後をexitにする。oracleに未来価格を使うこと自体は教師ラベル生成の設計であり、未来をinputへ混ぜることや評価区間から教師を作ることと区別する。val/testにはoracleラベルを作らない。実装は候補の結果を事前計算するが、policyに渡すのはその時刻までの特徴だけである。

MLPは64×64 ReLU、weighted BCEのpositive weightはtrain内hold数/exit数、AdamW lr.001・weight decay1e-5、8epoch、batch8192。これは [公式MLP script](https://github.com/Justinnnn0313/spx-short-straddle-rl/blob/main/scripts/train_exit_imitation_policy.py) の規模を参考にした。公式のrandom row validation splitとtorch seed欠落は採用せず、全乱数seedを指定する。

RLは共有trunkとscalar exit logit/value head、Monte Carlo return-to-go、normalized advantage、AdamW lr1e-5・value.5・entropy.001・warmのみBC anchor.01・grad clip1。gamma.997はこの適応では経過1時間あたりに定義し、実決済までの5分単位経過時間から割引する。報酬はterminal時の費用込みepisode return×100、途中は0とする。SPX固有VRP/CVaR/late penaltyは作らず、原典のshaped rewardとは異なる。

rawとwarmは各100更新×16episode＝1,600 sampled episode、固定slotの5分環境時間921,600本で同じbudget。訪問した有効判断数は早期決済で異なるためtraining.jsonに実数を分けて記録し、policy-gradient sample数まで同一とは主張しない。最低保有中のforced holdと最後のterminal時刻をreturnsに含め、無効actionをpolicy勾配から除く。reward配列末尾の行数slicingには依存しない。warmのMLP事前学習費用は別記する。公式AC defaultの1,000更新より小さいCPU試行で、最終論文設定の再現ではない。

seedは1/3/5/7/11。MLPはそのseed、RLのPython/NumPy/torchはseed+10000、episode抽出はseed+20000、bootstrapは45001に固定する。checkpointは固定予算の最終状態を使用し、val/testを使った選択はしない。検証区間も性能表示だけで、ハイパーパラメータを変更しない。

## 公開成果物と解釈

入力CSVは [SwingGuardの取得手順](../swingguard-research/README.md) で先に生成し、[入力hash](reports/inputs.json) と照合する。コードはレビュー後の満了open gap修正版で、公開用 [結果報告](reports/report_ja.md)、[全100run指標](reports/metrics.csv)、[集約](reports/summary.csv)、[paired集約](reports/paired_summary.csv)、[境界修正証拠](reports/terminal_fix.json)、[検証要約](reports/validation.json) を保持する。Gemini/LLM呼出は0件。

生データ、モデルweight、全台帳、runtime環境情報、旧before/after snapshotはGit対象外であり、全成果物は上記の新規学習・再評価手順で生成する。元実験から保存15weightだけを再評価した歴史的修正では、学習せず全100run・8,000取引・2,413,475資産行の指標/台帳がbyte一致した。公開コードからの新規実験は修正後の学習経路を使用し、旧ローカルweightの完全一致を無条件に約束しない。replay_terminal_fix.pyは元weight・snapshotを持つ場合の補助ツールで、新規再現には不要。

既存17件に満了境界8件を加え、最終25テストを実施した。満了openでもgap SL/TPを優先し、その足の後続high/lowを参照しない。

grossはnetと同じ数量の実現gross PnLを足し上げた費用加算戻しであり、費用ゼロで数量・複利を再計算した別戦略ではない。turnoverは売買fill notional合計/初期資産。Sharpeは5分return・年365日で年率換算し、自己相関を補正していない。DDは5分終値と決済後cashのgridで計算し、足内の最大DDではない。exposureは時価position/equityの時間平均。利益・DD・Sharpeは市場・期間・固定entryに依存する。

5seedは同じ市場経路を共有するため独立した市場標本ではない。paired difference、mean/std/median、探索的bootstrapを確認できても、統計的な優位は確立していない。新しい市場期間、walk-forward、多重比較補正、異なるentry・費用・保有期間による確認が必要である。過去データが他研究で見られていないことやモデルの完全なhistorical noveltyも保証できない。
