# EvoQuantLab-inspired offline adaptation

BTC/ETHの現物価格データで、時系列因子を安全なJSON式木から探索する研究用実装。LLM通信は0回で、原論文の再現・LLM性能検証・実運用利益ではない。実発注や資格情報を扱う機能はない。

完成結果は [report_ja.md](reports/report_ja.md)、全指標は [metrics.csv](reports/metrics.csv)、5seed集計は [summary.json](reports/summary.json) を参照する。`admitted_on_development=false`の候補は診断評価であり、採用方針は現金。現金の0収益を成功扱いしない。

基本条件の5seed平均純収益はdual GP +1.43%、fixed GP +0.36%、random +1.95%、noise GP +5.39%、B&H +62.44%。本適応版の優位性は示されなかった。最初のtest batchは極小移動を省いたCSV台帳の監査で停止し、出力処理だけを修正して同じ凍結設定を再計算した。計2回の技術的試行を[recovery.json](results/offline-v1/recovery.json)に明記。engine/設定/探索結果は不変で、既に出力された5口座のmetrics/equity/tradesはファイルhashが全て一致した。元source・freeze・中断結果も親結果ディレクトリに保持する。

## 研究の範囲

原典：[EvoQuantLab v2](https://arxiv.org/html/2610.17130v2)。研究方針を変える外側ループとGP因子探索の内側ループの分離、開発期間限定の探索、同予算null探索、1h追加遅延・片道5bp・72h平滑化を参考にする。多数銘柄の横断面研究をBTC/ETHの時系列研究へ置き換えており、横断面IC・carrier residualizationは使わない。詳細な式文法、split、探索予算、閾値は適応版独自の事前仮定。

入力は既存 `../swingguard-research/data/{BTCUSDT,ETHUSDT}-5m.csv` とmanifest。元取得基盤の公開Binance spot ZIP/CHECKSUMに紐付いたCSVを、そのhashと5分時刻・OHLC・連続性を再検査して読み取る。取得元を変更せず、追加ダウンロードもしない。5分足12本を1時間足へ集約し、2024-06〜2025-07末の10,224時間を使う。

6月は特徴量準備、trainは2024-07-01〜2025-02-01、validationは2025年2/3/4月の3fold、testは2025-05-01〜2025-08-01未満。全てUTC。train normalizerだけをfitし凍結。24h先ラベル＋バー確定1h＋追加遅延1hに対して末尾26hをpurgeし、各評価fold先頭も26hをembargoする。保有は逐次配分方式で最大期間を設けず、foldごとに現金開始・末尾全清算。test損益評価は2025-05-02 02:00〜2025-07-30 22:00の2,156時間。

買いと現金だけを使い、各資産の上限50%、合計100%以下。正規化した因子の正のtanh値を過去168hボラで縮小して72h平滑化し、確定足の1h後の始値で約定する。1万USDTから開始し、基本費用は片道5bp。手数料控除後の目標配分を達成する数量を計算して、費用・価格損益・現金・保有数量を保存する。空売り・借入・funding・perp清算はモデル化しないためHyperliquid実績とは呼ばない。注文サイズ下限・板・変動するslippage・市場インパクト・ステーブルコインリスク・税は未モデル化。

## 比較と予算

5 shared seeds（11,23,37,53,71）、3cycles×128=384評価/seed/方式を事前固定。

- `fixed_gp`: family重みを固定したGP。
- `dual_gp`: 決定論的offline proposerがdev evidenceだけから次cycleの探索family重みを変えるGP。
- `random`: 同じ文法・budgetの独立ランダム式探索。
- `noise_gp`: 検証の共同リターンを168hブロックで並べ替え、dual GP探索全体を同budget再実行するnull対照。

全て最初の30評価は共通の15 terminalと符号反転のstarter。残りも棄却attemptを含め記録する。式木は全ノードがdimensionless scalar score型で、depth4/nodes15、allowlistされたterminal/演算だけを受理し、`eval`/`exec`を使わない。異常な生成は最大32attempt/評価で停止。GPはトーナメント、部分木交叉、変異、構文重複排除、相関>0.95の繁殖archive除外、family別eliteを使う。相関制約は繁殖の多様性制約であり、最終dev winnerは評価済み全候補から選ぶ。詳細はreportとコードに記載。

目的関数は3foldの費用後Sharpe最小値−0.01×nodes。全foldのSharpe>0・約定件数>=10・DD>=−20%で採用判定し、閾値を成績で緩めない。各方式の全5seedをそのまま未使用期間で評価し、test成績による再選択は行わない。0/5/10/20bpと追加遅延1/2/4hの12感度条件も事前固定。B&Hは初期50/50購入後に配分をリバランスせず、入口/出口費用を払う。

`LLMProposer`は将来の構造化提案adapter用のインターフェースのみ。現状は明示エラーで通信しない。`OfflineProposer`の提案は次cycleのfamily優先度0.5〜2だけを変え、評価ルール、split、費用、閾値を変えない。原論文の外側LLM診断や知識獲得、完全なL0〜L6検証の代用とはしない。

## 公開成果物と入力準備

公開版はreportsの軽量指標・報告・選択バイアス診断だけを保持する。全探索履歴、NPZ、台帳、ローカル環境記録はGit対象外。入力CSVは [SwingGuardの取得手順](../swingguard-research/README.md) で先に生成し、[入力hash](reports/inputs.json) と照合する。LLM/Geminiは全過程で未使用。検証要約は [validation.json](reports/validation.json)。旧技術的復旧の詳細原本を前提とするrecover_serialization.pyは保存済みローカル成果物用で、新規再現には不要。

## 再実行

Python>=3.10とrequirements.txtのNumPy/pandasを使用。必要なら事前に `python -m pip install -r evoquantlab-research/requirements.txt` で依存を用意する。1CPU・数値ライブラリ1thread・process memory 1GiBを強制し、並列workerは作らない。制限を適用できなければ停止する。

repo rootのPowerShellから次を実行する。既存結果は上書きせず、未使用の出力名にする。これらは通信・発注・課金しない。

```powershell
Set-Location evoquantlab-research
$env:PYTHONDONTWRITEBYTECODE='1'
python tests/run_tests.py
.\run_all.ps1 -Output results/reproduce-v1
python run.py verify --output results/reproduce-v1
```

実行制限でPowerShell scriptを使えない場合は次を順に実行する。別のデータ置場は`prepare --source-dir <path>`で指定できる。

```powershell
python -u run.py prepare --output results/reproduce-v1 --source-dir ../swingguard-research/data
foreach ($method in @('fixed_gp','dual_gp','random','noise_gp')) {
  foreach ($seed in @(11,23,37,53,71)) {
    python -u run.py search --output results/reproduce-v1 --method $method --seed $seed
    if ($LASTEXITCODE -ne 0) { throw 'search failed' }
  }
}
python -u run.py evaluate --output results/reproduce-v1
python -u run.py verify --output results/reproduce-v1
```

`prepare`は価格を読む前に設定・code hashを事前登録し、development.npzとsealed_test.npzを分ける。各searchはdevelopmentだけを開き、Python audit hookでsealed_testのopenも拒否する。探索APIもtest時刻を含むpanelを拒否する。これは本実装のプロセス入力/API/file-open制約であり、OS全体のACLや第三者コードの安全性を保証するものではない。

`evaluate`は20探索の完了後に式木・normalizer・設定・code/data/search hash・感度gridをfreezeし、1回のtest batchを実行する。既存出力の再開封や凍結後の再探索を拒否する。開封後に問題が出ても設定を調整して同じtestを再利用しない。同じ凍結コード/設定で新規outputへ再実行するのは計算再現性の確認であり、新たな独立OOS実験ではない。

## 成果物と検証

再実行は指定outputに以下を生成する。`results/offline-v1`直下は元の事前登録・中断したtest・元ソースsnapshot・復旧履歴である。

- `pre_registration.json` / `settings.json`: データ読取前の実験設定・source hash・資源制限。
- `data_manifest.json`: 入力CSVと集約済みパネルhash、完全性・期間・本数。
- `search/*/{attempts,trials}.jsonl`: 全生成・棄却・評価の履歴。`selection.json`にdev winnerとtrain-only normalizer。次cycle提案とpolicy hashも保存。
- `freeze.json` / `holdout_access.json`: test前凍結と1回の一括開封記録。
- `metrics.csv` / `metrics.json`: 全244行（4方式×5seed×12条件＋B&Hの4費用）。純損益・DD・Sharpe・turnover・trades・cost等。
- `backtests/*`: 基本条件21口座の`trades.csv` / `equity.csv` / `metrics.json` / 全非ゼロ移動の`account_movements.csv` / 独立数量台帳の`ledger_audit.json`。tradesの件数は元から固定の資産1e-10超の約定を数え、全移動台帳で極小値も含む会計を厳密に閉じる。
- `temporal_ic.json`: 銘柄ごとの時系列24h先Pearson IC。重複ラベルがあるため推測統計には使わない。
- `selection_bias_audit.json`: 5本の探索全体nullによる選択バイアス診断。最小p=1/6で、有意性を主張しない。
- `summary.json` / `report_ja.md` / `verification.json` / `test_results.txt`: 集計・日本語報告・成果物整合性・合成検査結果。

検査は重要なリスクに絞る。全terminal/operatorのprefix不変性、future shock、train-only fit、forward labelとpurge/embargo、追加遅延、悪意ある式木拒否、fee付き手計算口座、独立現金/数量replay、B&Hの不要再配分なし、費用感度の単調性、nullの共同行保持、同seedの完全な探索履歴再現、合成データでのfreeze→test→全grid/台帳検証、再開封・凍結後探索の拒否を確認する。

全seedは同じ市場区間なので独立した市場標本ではない。短いtest、二銘柄、既存研究で利用された歴史データであり、学習済みモデルや研究者に未知とは保証しない。実装の正常性と将来収益性は別々に判断する。

初回の復旧手順は`python recover_serialization.py --output results/offline-v1`で、既に完成済みのため再実行を拒否する。このスクリプトは元sourceと現在sourceを比較して`save_account`以外の変更を拒否し、元候補をコピーして出力のみ再計算する。修正版による通常の新規再現実行では復旧処理は不要。
