# Hyperliquid AI Trader 今後の実装方針 v2

## 1. この文書の目的

本書は [housin.md](housin.md)、[reviewed_housin.md](reviewed_housin.md)、その後の議論をまとめた新しい実装方針の例である。既存の2文書は経緯として残す。本書は今後の設計案であり、以下の変更がすでに実装されているという意味ではない。

目指すものは、同じTrader、Reviewer、Risk Engineを使い、過去データからDry-run、Testnetまで一貫した方法で検証できる研究環境である。

中心となる問いは、これまでと同じである。

> LLMは、価格の上下を説明するだけでなく、取引コストを支払っても利益を期待できる局面を選べるか。Reviewerによるstrategyの更新は、その判断を未知の期間でも改善するか。

今回は次の条件を基本とする。

- 過去データはBinanceのBTCUSDT USD-M無期限先物を使用する。現物やCOIN-Mとは混ぜない。
- Hyperliquidでも同じ計算で作れる特徴量をTraderの共通入力にする。
- Traderは5分ごとの判断を担当し、口座管理や直近取引からの学習は担当しない。
- Reviewerは1日1回、判断時点までに確定した結果から`strategy.json`の変更を提案する。
- `strategy.json`だけを、運転中にLLMが更新できる学習可能領域とする。
- 資金管理、注文制限、実際の発注可否はPython側で決める。
- promptの初期比較はReviewer OFFで行い、有望な候補だけ連続Replayへ進める。

ここでいう「学習」は、LLM本体の重みを変更することではない。取引結果から市場の読み方を`strategy.json`へ残し、翌日のTraderが使うことである。

## 2. 以前の方針から変える点

| 項目 | 以前の方針・提案 | v2で採用する方針 |
|---|---|---|
| 過去データ | Hyperliquidの板やasset contextも使う最終Replay | まずBinanceの過去データで成立する研究基盤を作る |
| Traderの特徴量 | 価格、出来高、板、funding、OIなど | 初期版は確定した1分足の価格・出来高から作る共通特徴量 |
| Traderへの取引履歴 | accountやrecent closed tradesも渡す | 渡さない。accountはRisk Engine、取引履歴はReviewerが使う |
| Reviewerの間隔 | 現行の2時間間隔 | UTCの日次区切りで1日1回 |
| strategy | 市場仮説・ルールを更新 | 同じ基本項目を残し、件数・文字数・数値範囲・更新根拠を制限する |
| Batch評価 | 1日分をまとめられるという提案 | 各判断が独立する条件を明記し、判断生成と口座の時間順処理を分ける |
| 約定の再現 | best bid/askを前提とする | 板履歴がない場合は価格とコストの仮定による近似と明記する |
| Hyperliquidでの評価 | 過去データReplayを必須にする | 履歴未確保をBinance研究の開始条件にしない。ただしHyperliquidでの利益は別途検証する |

未来情報を使わない、費用控除後で評価する、単純ルールと比較する、Locked Testを使い回さない、Testnetの安全境界を維持する、というレビュー版の基本原則は引き継ぐ。

## 3. BinanceとHyperliquidで何を共通にするか

### 3.1 「同じ項目がある」と「同じ市場」は別である

共通にするのは、特徴量の名前、意味、単位、計算方法、利用可能時刻の扱いである。価格や出来高の値が一致するという意味ではない。

BinanceのBTCUSDTとHyperliquidのBTC perpetualでは、参加者、出来高、価格、担保・決済通貨、手数料、funding、約定条件が異なる。USDTとUSDCも無条件に同一視しない。

したがって、Binanceでの好成績は「Hyperliquidへ持ち込んで検証する価値のある候補」であり、Hyperliquidの実績ではない。Binance価格にHyperliquid向けのコストを適用した結果も、移植時の仮定を置いた試算として別に表示する。

### 3.2 初期版の共通データ

Binanceの公開USD-M KlinesにはOHLC、出来高、取引件数などがある。Hyperliquidの`candleSnapshot`にも対応する足情報がある。初期版はこのうちOHLCVを基本とする。[Binance公開データ仕様](https://github.com/binance/binance-public-data#futures)、[Hyperliquid Candle snapshot](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot)

OHLCVは、1本の足の始値・高値・安値・終値・出来高をまとめた呼び方である。

| 共通データ | Binance USD-M Klines | Hyperliquid candles | 扱い |
|---|---|---|---|
| 足の開始・終了時刻 | Open time / Close time | `t` / `T` | UTCミリ秒と足の区間へ統一 |
| 始値・高値・安値・終値 | Open / High / Low / Close | `o` / `h` / `l` / `c` | 同じ計算処理へ渡す |
| 出来高 | Volume | `v` | BTC数量への対応を取得データで検証し、直近との比較に使う |
| 取引件数 | Number of trades | `n` | 追加候補。初期の必須項目にはしない |

取引件数は取引所ごとの約定の数え方の差があり得るため、両方に項目があるだけで同じ分布とはみなさない。採用する場合は`trade_count_zscore`として、各取引所内の直近との比較を検証する。

HyperliquidのCandle snapshotは直近5,000本の制限がある。1分足の長期履歴を後から無制限に取得できる前提にはせず、将来の比較用データは継続保存する。[Hyperliquid公式仕様](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#candle-snapshot)

### 3.3 既存のBinanceデータに関する注意

[既存のBinance研究README](../binance-btcusdt-futures-research/README.md)に記載された対象は、15分足・1時間足・日足とFunding Rate、Metricsである。これは1分足が取得済みであることを保証しない。

5分ごとの判断、1分・5分return、最大5分保有、足の途中のTP/SLを検証するには、対象期間の1分足を追加取得・検証する必要がある。15分足を分割・補間して1分足を作ってはいけない。

最初に既存manifestと実ファイルを調べ、1分足の有無、期間、欠損率を確認する。過去にすでに分析した期間は原則DevelopmentまたはValidationとし、新しいLocked Testとして扱わない。

## 4. 共通の特徴量

特徴量とは、Traderへそのまま大量の足を渡す代わりに、価格や出来高を短い数値へまとめたものである。既存の`features.py`の名前をできるだけ残す。

### 4.1 初期版で採用する項目

以下は本書で固定する計算案である。APIが直接返す値ではなく、共通のPython処理で計算する。

| 特徴量 | 意味 | 計算と単位 |
|---|---|---|
| `return_1m` | 直近1分の騰落 | 最新確定終値 ÷ 1分前の終値 − 1 |
| `return_5m` | 直近5分の騰落 | 最新確定終値 ÷ 5分前の終値 − 1 |
| `return_15m` | 直近15分の騰落 | 最新確定終値 ÷ 15分前の終値 − 1 |
| `return_60m` | 直近60分の騰落 | 最新確定終値 ÷ 60分前の終値 − 1 |
| `realized_vol_5m` | 直近5分の値動きのばらつき | 直近5個の1分returnの母標準偏差。年率換算しない |
| `realized_vol_30m` | 直近30分の値動きのばらつき | 直近30個の1分returnの母標準偏差。年率換算しない |
| `atr_pct` | 最近の1分足の値幅 | 下記の14本平均値幅 ÷ 最新確定終値 × 100 |
| `volume_zscore` | 出来高が直近よりどれくらい多いか | 最新足の出来高と、その直前60本の平均との差を、同60本の母標準偏差で割る |

returnとrealized volatilityは比率で、`0.003`は0.3%である。`atr_pct`とTraderのSL/TPは百分率の数値で、`0.30`は0.30%である。`bps`は1が0.01%を表す。これらを混ぜない。

ATRの1本分の値幅は、`高値−安値`、`|高値−前の終値|`、`|安値−前の終値|`の最大値とし、直近14本を単純平均する。既存実装に合わせ、Wilder方式などへ黙って変更しない。

現行の`atr_pct`はmark価格で割っている。共通版では最新確定終値で割るため、特徴量の仕様変更としてversionを付ける。過去のstrategyやcacheと無条件に混ぜない。

`volume_zscore`の標準偏差が0なら、既存の扱いに合わせて0とし、計算上の例外であることを品質記録へ残す。足が欠けている場合とは区別する。必須足の欠損を0埋めして計算しない。

### 4.2 初期版では使わない項目

| 項目 | 初期版で外す理由 | 将来の扱い |
|---|---|---|
| `book_imbalance`、板の厚み | 1分足から過去の板は復元できない | 両方の対応履歴がある別実験で評価 |
| 実測`spread_bps` | OHLCの高値−安値はspreadではない | 発注時の安全確認には使う。共通の方向判断の必須特徴量にはしない |
| OIとOI変化率 | 利用期間・更新頻度・単位を揃える確認が必要 | 履歴を確認して追加。粗い系列から5分変化を捏造しない |
| funding rate、funding z-score | 予測値と確定値、対象時間、支払時刻が異なる | 方向判断への追加は別実験。費用としての扱いは第10節 |
| premium、mark/oracleの差 | 取引所固有の価格の定義がある | 初期共通strategyには入れない |
| `impactPxs`、予測funding | 同じ意味の過去入力を確保していない | 将来のHyperliquid向け研究候補 |
| taker buy量・比率 | 今回採用する両方のcandle入力に共通ではない | 対応データを確保した別実験 |

これらが無価値という意味ではない。「現在APIで取れる」と「今回の過去データで当時の入力を再現できる」を分けるためである。

特徴量から外しても、Hyperliquidでの注文前の板確認やRisk Engineの安全確認は外さない。方向判断の情報と、注文を安全に実行できるかの確認は別の責務である。

### 4.3 共通の生成処理

BinanceとHyperliquidのAdapterで生データを共通の足形式へ変換し、その後は同じ特徴量生成処理を使う。

現在の`build_market_features()`は板がないと失敗し、mark、oracle、funding、OIも引数に取る。そのまま使用可能とはしない。確定足から作る共通部分と、取引所固有の観測・発注確認を分離する。

不足した板を架空の厚みで埋めたり、OIやoracleを0で埋めて正常な市場観測に見せたりしない。データ形式では任意項目または別の実行用情報として表し、共通Trader入力には含めない。

## 5. Trader・Reviewer・Risk Engineの役割

| 担当 | 入力 | 決めること | 決めないこと |
|---|---|---|---|
| Trader | 共通特徴量、推定cost、その日のstrategy、固定SL/TP制限 | side、SL、TP、confidence、would_abstain、thesis | size、leverage、口座管理、strategy更新 |
| Reviewer | 現在のstrategy、確定取引と判断時の入力、Python集計 | 次に使うstrategyの変更提案 | prompt、Risk Engine、fee、実行モデルの変更 |
| Risk EngineとPython側の発注判定 | Traderの提案、口座、固定設定、発注時の市場情報 | 見送り条件、size、上限、安全確認 | 市場仮説の自由な変更 |
| Execution Simulator | 注文計画、過去価格、固定した約定仮定 | 仮想約定、TP/SL、funding、口座更新 | Traderの判断を後から修正すること |

`TradingService`、`TradingExchange`、`TradingTools`の境界は活用する。ただし現在の`run_once()`は取引履歴とaccountをTraderへ渡しているため、判断生成と発注処理を分ける変更が必要である。

Traderには口座残高、現在のposition、日次損益、recent closed tradesを渡さない。関数呼び出し経由でも取得させない。これらを見た独自の学習や資金管理が混ざらないようにする。

Traderの出力は既存名を基本とする。

```json
{
  "side": "long",
  "stop_loss_pct": 0.30,
  "take_profit_pct": 0.60,
  "confidence": 0.55,
  "would_abstain": false,
  "thesis": "直近の上昇と出来高増加が一致しているという仮説。費用と値幅を比較して判断した。",
  "abstain_reason": ""
}
```

これは形式の例であり、推奨する売買条件ではない。

`confidence`は「提案したside・SL・TP・固定max holdで約定できた場合に、費用控除後の損益が正になる主観確率」とする。約定の成功確率や説明文への自信ではない。

見送る場合も比較用のside・SL・TPを返し、`would_abstain=true`と理由を記録する。初期の主比較は`mandatory_entry=false`とし、見送りが本当に発注停止につながるようにする。強制参加は別の比較条件とする。

SL/TPは固定の許可範囲内でTraderが選ぶ。範囲外・不正な応答はPythonが拒否して記録する。黙って大きく補正するとconfidenceの対象が変わるため、初期版では自動補正を使わない。補正を導入する場合は別versionで、元の値と適用値を残す。

## 6. constitution.mdとstrategy.json

### 6.1 constitution.mdは固定仕様

`constitution.md`には、役割、目的、costの扱い、confidenceとwould_abstainの意味、SL/TPの単位、使用可能な関数、未来情報の禁止、Risk Engineとの権限境界を記載する。

相場ごとの判断ルールはstrategyへ置く。Reviewerにconstitution、Trader prompt、Reviewer promptを書き換えさせない。

### 6.2 strategy.json v2の初期例

以前から使っている`market_hypothesis`、`active_rules`、`failure_modes`、`confidence_calibration`を残す。今回の初期v2では、信号の重みや新しいルール言語を一度に増やさず、この4領域を学習の中心にする。

```json
{
  "schema_version": 2,
  "feature_set": "common_candles_v1",
  "version": 1,
  "parent_version": null,
  "market_hypothesis": "短期BTC価格では、直近の騰落・値動きの大きさ・出来高の組み合わせによって、費用を上回る取引機会を選べる可能性がある。まだ検証前の仮説である。",
  "active_rules": [],
  "failure_modes": [],
  "confidence_calibration": {
    "long": 0.0,
    "short": 0.0
  },
  "last_review_cycle": 0
}
```

`schema_version`はファイル形式、`feature_set`は特徴量の定義、`version`は学習による更新番号を表す。`parent_version`は更新前の番号である。これらはPythonが管理する。

`market_hypothesis`は現在の市場仮説、`active_rules`はTraderが判断時に参考にするルール、`failure_modes`は失敗しやすい条件である。足の生データ、現在の価格・残高、日ごとの日記は保存しない。

ルールを追加した後の1件の形は、例えば次とする。数値とIDは説明用の架空例である。

```json
{
  "id": "rule-001",
  "text": "return_5mとreturn_15mが同方向でvolume_zscoreが正のときは、その方向への継続を検討する。ただしatr_pctに対して往復costが大きい場合は見送りも検討する。",
  "features": ["return_5m", "return_15m", "volume_zscore", "atr_pct"],
  "evidence": {
    "trade_ids": ["trade-101", "trade-108"],
    "summary_id": "review-summary-007",
    "observation_count": 24,
    "reason": "対象条件の確定取引を集計し、cost控除後の結果を確認した。"
  }
}
```

ルールはPythonコードとして実行せず、Traderへの短い指針として扱う。`features`は使用した共通特徴量を明示するための欄である。JSONの検証だけで自由文の意味を完全に保証できるわけではないため、promptでも未提供情報の使用を禁止し、出力を監査する。

### 6.3 記憶量と更新範囲を制限する

以下は初期の制限案であり、Developmentで調整してから評価条件として固定する。

| 項目 | 初期制限案 | Reviewerによる変更 |
|---|---|---|
| `market_hypothesis` | 1件、400文字以内 | 可 |
| `active_rules` | 最大8件、各本文240文字以内 | 根拠付きで追加・変更・削除可 |
| `failure_modes` | 最大8件、各本文240文字以内 | 根拠付きで追加・変更・削除可 |
| `confidence_calibration.long/short` | 各−0.2〜+0.2、1日変化量は各0.02以内 | 根拠付きで可 |
| ファイル全体 | UTF-8で16 KiB以内 | Pythonが検証 |
| version、parent、feature_set、schema | 固定仕様または自動採番 | 不可 |
| 根拠件数、時刻、集計結果 | 保存済み証跡から算出 | LLM申告値をそのまま信用しない |

新しいルールを追加すると上限を超える場合は、既存ルールの統合・削除を同時に提案する。根拠取引の全件はSQLiteへ残し、strategyには代表IDを最大5件と集計IDを置く。これにより根拠も際限なく増やさない。

`last_review_cycle`は最後に受理されたstrategy変更の対象cycleとする。変更なしのレビューや失敗も、strategyとは別のreview記録に残す。

### 6.4 confidenceの補正を二重にしない

`confidence_calibration`の名前は変えず、LONG/SHORTごとの加算補正とする。

1. Traderは補正値を除いたstrategyの市場仮説・ルールを受け取り、元のconfidenceを返す。
2. Pythonが該当方向の補正を一度だけ足し、0〜1へ収める。
3. 発注のconfidence判定には補正後を使う。
4. 元の値、補正値、補正後の値をすべて保存する。

例えば元が0.65、LONG補正が−0.05なら、発注判定に使う値は0.60である。LLMにも同じ補正を適用させてからPythonでも引く、という重複を避ける。

補正によって正確な確率になる保証はない。confidenceの区間ごとの件数、実際の費用控除後勝率、平均損益を確認し、補正なしとも比較する。

### 6.5 前回提案した追加領域の扱い

`signal_weights`、`regime_policy`、`entry_policy`、`exit_preferences`は将来の追加候補として残す。初期v2では使わず、必要性を別々に比較する。

- momentum・volatility・volumeのどれを重視するかは、まず`active_rules`で表す。
- trend・range・高volatility・低volatilityの違いも、まず同じ特徴量を用いた短いルールで表す。
- ATRを基にしたSL/TPの好みはルールへ書けるが、固定のSL/TP上限やmax holdは変更できない。
- confidenceの発注閾値とcost条件はPythonの固定設定へ置く。Reviewerが変更できる範囲と混ぜない。
- book、OI、funding、premiumの重みは、初期共通特徴量にないので追加しない。

数値の重みや条件式を追加するときは、その値を誰がどう適用するか、競合時の優先順位、変更範囲を先に定義する。欄だけ増やして効果を曖昧にしない。

## 7. Reviewerは1日1回

### 7.1 1日の流れ

1. その日に使うstrategyのversionを固定する。
2. Traderは5分ごと、最大288地点で同じversionを使って判断する。
3. Risk EngineとExecution Simulatorは時間順に発注可否・約定・損益を処理する。
4. UTC日次境界で、その時点までの確定結果をPythonが集計する。
5. Reviewerが1回だけ変更または変更なしを提案する。
6. Pythonが検証し、受理したstrategyを次の日の判断から使う。

`review_interval_seconds=86400`とし、起動から24時間ではなくUTC日次境界を基準にする。途中起動日は短い集計区間であることを記録する。

同じ境界時刻では、既存注文・決済・fundingなどの処理と証跡の確定、日次集計、review、次の判断の順に処理する。決済とfundingの競合は取得元の仕様に合わせて実行設定へ固定する。

日をまたいで未決済の取引はレビューの確定損益へ含めず、決済後のレビューへ送る。その取引にはエントリー時のstrategy versionを保持する。

Liveでレビュー完了が次の判断に間に合わない場合、初期案ではその日は旧strategyを使い続け、受理済み更新を次の日次境界まで保留する。日中にversionを切り替えず、遅延と適用時刻を記録する。Replayでもこの規則と固定したレビュー遅延仮定を使う。

### 7.2 Reviewerへ渡す情報

- 現在のstrategyと前回レビュー以降の新しい確定取引。
- 各取引の判断時点の共通特徴量、推定cost、元と補正後のconfidence、SL/TP。
- gross PnL、fee、funding、net PnL、決済理由。
- MFE/MAE。保有中に最も有利・不利に動いた幅という意味で使う。
- 見送り判断について「取引していたらどうなったか」を同じ仮定で計算した結果。
- 当日、直近7日、直近30日のPython集計。

取引を最大100件などで切ってから日次全体として扱わない。対象期間の全件を取得し、LLMへ渡す量は集計と代表例で減らす。代表例は勝ち・負け・方向・confidence区間など事前に決めた基準で選ぶ。

7日・30日分がまだない場合は存在する範囲だけを集計し、実際の日数を表示する。集計にはstrategy versionも付け、古いstrategyの結果を現在のルールの証拠と混同しない。

### 7.3 Patchの受理条件

既存の`apply_strategy_patch()`の「許可した場所だけ変更できる」という考え方を維持し、v2形式の検証へ拡張する。

- 親versionが一致すること。
- 根拠IDが実在し、対象runのレビュー時刻までに確定していること。
- 根拠の件数・重複・集計値をPythonで確認すること。
- 初期案としてルール変更は関連する確定観測20件以上、confidence補正は対象方向の確定予測50件以上を要求する。
- 初期案として1日最大2件の変更とし、文字数・数値範囲・許可項目を検証する。
- 全変更をまとめて検証し、一部だけ適用しない。
- 不正応答、API障害、証拠不足の場合は旧strategyを維持する。

20件・50件は統計的有意性を保証する数字ではなく、少数例への急な変更を抑える初期制限である。相互に重なる観測の件数を独立した証拠の数とはみなさない。変更なしも正常な結果である。

同じ取引を翌日も新規件数として数え直さない。削除・統合にも理由と根拠を残す。受理されたこと自体を「改善の証明」とは呼ばない。

## 8. Batch評価と連続Replay

### 8.1 promptの初期比較

最初から全候補を1年間連続実行しない。Developmentだけから3,000〜5,000地点を選び、Reviewer OFF・共通の初期strategyで比較する。

上昇・下落、値動きの大小、出来高の大小など、判断時点までの特徴量で局面を分け、偏らないように地点を選ぶ。これが以前の議論に出たstratified Batch評価の意味である。

選択地点、分類基準、乱数seed、候補数上限を固定する。全候補に同じ地点とcost条件を使い、後の利益で地点を選ばない。局面を均等に抽出した結果は実際の出現頻度と異なるため、全期間の期待利益と同一視しない。

この段階では、独立した仮想取引の費用控除後結果、見送り、confidenceを比較する。日次損失制限、連続保有、複利を反映した口座成績ではない。drawdownや年利をこの点評価から作らない。

### 8.2 1日分をまとめられる条件

1日分のTrader判断をまとめて生成できるのは、次の条件をすべて満たす場合だけである。

- 各リクエストが、その時刻までに利用可能な市場情報だけで完結する。
- 当日のstrategyが固定されている。
- Traderへaccount、直前の取引結果、当日損益を渡さない。
- Trader用のcost見積りが、当日まだ決まっていないsizeや約定結果に依存しない。
- 呼び出せる関数や共有会話履歴からも過去の判断結果を参照しない。

まとめるのはTraderの回答生成だけである。回答を時刻順に並べ、Risk Engine、約定、口座更新は順番に実行する。日次損失制限などで発注できない回答は、生成済みでも未実行として残す。

Reviewer ONでは、当日のReplayと日次reviewが終わるまで翌日のstrategyが決まらない。そのため複数日を一度に独立処理しない。Liveでは未来の足がないため、翌日の288判断を先に生成することもできない。

Batch APIの対応モデル、Function Calling形式、応答期限、料金・quotaは実装時に利用先の公式仕様で確認する。利用できない場合は、同じ入力を制限付きの通常API呼び出しで処理する。Batchの実待ち時間をそのまま市場内の約定遅延とはしない一方、Replayでは別途固定した判断・送信遅延を必ず適用する。

### 8.3 有望候補だけ連続Replayへ進める

点評価の上位候補について、まずDevelopment内で30〜60日の連続Replayを行う。その後、時系列をずらしたValidationで比較し、最後の候補を選ぶ。

順序は、単純ルール、LLM/Reviewer OFF、LLM/Reviewer ONとする。日ごとにstrategyが更新される効果は連続Replayで測り、点評価と混ぜない。

## 9. Historical Datasetと時間の扱い

### 9.1 先にデータの充足を確認する

Replayを作る前に、次をmanifestへ記録する。manifestとは、使用データと条件を再現するための一覧である。

- 取得元、市場、銘柄、足間隔、対象期間、取得日時。
- 各列の意味、単位、timestampの単位、欠損、重複、連続性。
- 元ファイルと正規化後ファイルのSHA-256。
- 使用する特徴量のversionと必要な事前期間。
- fundingや板について、実測履歴があるか、仮定しかないか。
- 公開後の訂正や公開遅延を完全には再現できない場合、その限界。

初期特徴量は少なくとも連続した確定1分足61本を必要とする。評価開始前の計算用データは使ってよいが、その期間の損益を評価区間へ混ぜない。

### 9.2 available_atを守る

`event_time`は市場の事象が起きた時刻、`available_at`はBotがその情報を使える時刻である。

例えば09:00〜09:01の足の高値・安値・終値は、09:00:30のTraderには渡せない。足の確定境界以後に、設定した配信遅延を加えて利用可能にする。Binanceでは既存研究の`close_time + 1ms`という境界を引き継ぎ、単位と遅延を明記する。

Historicalで確認できない実際の配信遅延は仮定として固定する。後日ダウンロードした時刻を当時の配信時刻とはみなさない。

すべての観測で`available_at <= decision_time`を検証する。欠損を未来の値で埋めない。正規化の平均・標準偏差もその時点より前だけで計算する。

### 9.3 仮想時計と保存

`HistoricalReplayExchange`と`ReplayRunner`を追加し、実時間のsleepではなく仮想時計を進める。5分の判断間隔の間も、約定、TP/SL、funding、max holdを処理する。

市場データはParquet、decision・order・fill・review・strategy・損益の証跡はSQLiteへ分離する。巨大な生データや秘密情報はGitへ入れない。

## 10. Execution Simulatorとコスト

### 10.1 板履歴がない場合の初期モデル

1分足だけで、過去のbest bid/ask、板の厚み、IOCの実際の約定確率は復元できない。初期版は「足価格に、事前設定したspread・slippage・遅延を適用した近似」とする。

判断には確定足だけを使い、Entryには判断完了・送信遅延後の最初に利用できる価格を使う。1分足しかない場合の初期案は、その時刻以後で最初の足の始値とする。将来の始値はSimulatorが時間を進めて初めて読む。

有利な始値や足の途中の価格を選ばない。判断に使った終値で無遅延約定したことにもしない。分単位へ遅延を切り上げる近似の影響を報告する。

基準価格に対して、買いは半spreadとslippageを上乗せし、売りは同じ要素を差し引く。Exitも取引方向に応じた不利側を使う。実測の板がある別モデルではLONG entryをask、SHORT entryをbid、決済をその反対側とする。

足の出来高だけからIOCの完全約定を証明しない。固定した小額注文の近似として注文上限を設定し、全量約定仮定、拒否・部分約定の追加シナリオを区別する。これらの発生率を実測値と呼ばない。部分約定後の取消・残positionの強制決済も検証する。

### 10.2 TP・SL・max hold

max holdの初期値は既存方針と同じ300秒とし、実際のEntry約定時刻から数える。次の5分slotになっただけで300秒経過した扱いにしない。前のpositionが残るときは固定ルールで新規Entryを拒否し、cleanupで保有時間を無言で短縮しない。

同じ足でTPとSLの両方に触れた場合は`AMBIGUOUS`、すなわち順序不明とする。主結果はSL先の悲観評価とし、TP先との差と件数も表示する。境界へちょうど触れた場合も判定へ含める。

TP/SLはトリガー価格で必ず約定するという意味ではない。窓開けでは不利な始値、通常の到達では固定したmarket exitコストを反映する。足内の到達順、トリガー時刻、max holdとの競合が分からない場合も曖昧として記録し、事前規則を使う。

1分未満の正確な保有時間やmark価格によるトリガーをtrade OHLCだけで再現したとは言わない。初期版の秒単位設定と価格の近似を明記し、必要な精度はtrade履歴などを確保した追加モデルで検証する。

MFE/MAEもEntryからExitまでの範囲で計算する。決済後の値動きを含む足しかない場合は正確な値とせず、足単位の近似・上下限または不明として扱う。

### 10.3 fundingは特徴量と支払いを分ける

fundingをTraderの共通特徴量から外しても、保有中の費用から省略しない。

- Binanceの再現では、取得・検証できたBinanceの支払時刻と確定rateを使う。
- 将来確定する支払額は、支払イベント処理には使えるが、過去のTrader入力へ逆流させない。
- Hyperliquid向け試算では、支払間隔やrateをBinanceと同じとせず、別の費用シナリオとして固定する。
- 履歴が不足する場合は0に決めつけず、不足または仮定ありと記録する。仮定だけのrunを完全な実績再現の合格にしない。
- 最大5分保有でも支払境界をまたぐ可能性があるため、無視しない。

### 10.4 コスト見積りと損益

Traderへ渡す`estimated_round_trip_cost_bps`は、往復fee、想定spread、Entry/Exitの想定slippage、必要なfunding予算を含む費用見積りとする。各内訳と、実測か設定値かを記録する。

初期Batchの見積りは固定した参照注文額を前提とし、後から決まる口座sizeに依存させない。実際の注文額と発注時のコストが許可範囲を外れる場合はPythonが拒否する。

```text
net PnL = 実際の約定価格で計算したgross PnL − entry fee − exit fee ＋ funding受払額
```

spreadとslippageはすでに約定価格へ入っているので、さらに二重に控除しない。見積りと実現コストを分けて報告する。

通常・高コスト・大きな遅延などの条件をDevelopmentで決め、良い結果だけを採用しない。feeや注文精度などの値は研究設定へ置き、Reviewerには変更させない。

`expected_move`は現行出力にないため、初期版では新しい数値出力を要求しない。TP幅を期待値と同一視しない。最初はcostを見たTrader判断とconfidenceによる発注判定を比較し、期待値/cost比による追加条件は出力仕様ごと別実験にする。

## 11. 見送りの評価

`would_abstain`の有効性を見るため、見送った地点でも「提案したside・SL・TPで取引していたら」という仮想結果を計算する。これが以前のcounterfactual abstainに対応する。

同じ約定モデル、費用、max hold、曖昧判定を使い、実口座とは別の固定参照額で比較する。この仮想取引の利益を実口座のPnLへ加えない。

仮想結果が確定するのは、TP/SLまたはmax holdまで時間が進んだ後である。日次レビュー時点で結果が未確定なら翌回以降へ送る。API障害・欠損・Risk拒否を、Traderが適切に見送った証拠へ混ぜない。

Reviewerには実取引と仮想結果を別集計で渡す。仮想結果しか根拠がない変更はその旨を残す。多数の仮想観測があっても、実口座で成立する取引回数や利益を保証しない。

## 12. データ分割とstrategyの引き継ぎ

### 12.1 評価の順序

1. Development：特徴量・prompt・制限値を検討する期間。
2. Walk-forward Validation：過去側で決めた候補を、その先の期間で繰り返し比較する。
3. Locked Test：最終候補を固定してから初めて成績を見る期間。
4. Dry-run：現在の市場で接続と判断を確認する。注文や仮想損益の検証ではない。
5. Testnet CanaryとForward：少数注文の安全確認後、固定候補で将来の動作を確認する。

ランダム分割は使わない。境界前には`max_hold + 最大Entry待ち時間 + 決済遅延余裕`を考慮して新規取引を止め、positionと仮想評価の結果が境界をまたがないようにする。この空白が従来のpurgeに当たる。

特徴量の計算に必要な過去足を次区間から参照することは許可するが、その将来結果を学習へ戻さない。

### 12.2 strategyの初期化と引き継ぎ

promptの点評価は、すべて同じ`initial_strategy.json`・Reviewer OFFで開始する。

連続Replayの主評価は、過去側で正規のReviewerが作ったstrategyを次期間へ引き継ぐ方法とする。開始時に使ったstrategyのhashを保存し、未来側のレビュー結果を過去foldへ戻さない。

各foldと各独立試行は、指定の初期状態から当該時点まで再生して作る。他の候補、別試行、未来まで進めたfoldのstrategyを流用しない。重複する評価期間の利益を足し合わせて一つの運用成績にもしない。

直近7日・30日のReviewer集計も更新に影響する状態である。これらはLLMが編集する学習領域ではないが、開始時の証跡範囲・重複排除位置・hashをstrategyと一緒に記録する。

### 12.3 Reviewerの比較条件

| 比較条件 | strategy | Reviewer | 確認したいこと |
|---|---|---|---|
| Static | 開始時の状態を固定 | OFF | Traderだけの成績 |
| Adaptive | 日次更新を引き継ぐ | ON | 更新と記憶の増分効果 |
| 更新なし | 同じ状態を維持 | 呼ぶが空patchのみ | 呼び出しや実装差が成績へ混ざっていないか |
| 毎日初期化 | 毎日、初期strategyから日次提案を作る | ON | 過去のstrategyを積み重ねる価値 |

「毎日初期化」は、日末に初期strategyとその日の確定証拠だけを使って翌日用strategyを作り、過去のstrategyや7日・30日集計は引き継がない条件と定義する。更新直後に消してTraderに一度も使わせない設計にはしない。この比較は累積記憶全体の効果を見るもので、strategyファイル単独の効果と断定しない。

version変更後に翌日の利益が増えても、市場が変わった可能性がある。同期間・同条件のOFF比較と複数試行を優先する。

### 12.4 Locked Test

開く前に、prompt、モデル、生成設定、特徴量、strategy開始状態、Reviewer集計の開始状態、日次更新規則、patch制限、risk、cost、約定モデル、遅延、データ、分割、コード、試行数、合格条件を固定する。

Test中も、事前に固定したReviewerが、その時点までの確定結果からstrategyを更新するのは許可する。人間が途中でpromptやルールを直した場合、その期間は以後Validationとして扱う。

通常の`replay`や`walk-forward`からTestを選べないようにし、`sealed-test`だけがcandidate lockとhashを確認して開けるようにする。実行後は消費済みTest ID、試行数、結果hashを保存する。

LLMが過去相場を学習済みである可能性は完全には除けない。Traderへ絶対日時・イベント名・不要な絶対価格を渡さず、相対的な特徴量を優先する。これだけで漏洩がなくなるとは言わず、固定後の将来期間による検証を別途行う。

## 13. 比較指標、失敗、再現性

### 13.1 最低限の比較相手

取引しないAlways abstain、5分momentumまたはmean reversionの単純ルール、LLM/Reviewer OFF、LLM/Reviewer ONを同じデータ・risk・costで比較する。ランダムsideも不具合検出用に加えられる。

初期版には板やOIがないため、それらを外す比較はまだ行わない。まず出来高なし、cost説明なし、confidence補正なし、見送りなしなど、一度に一つの変更を比較する。

### 13.2 指標と合格条件

主指標は未知期間の費用控除後の平均損益とし、取引あたり・判断機会あたりの両方を見る。net PnL、net return、最大drawdown、件数、取引頻度、方向別・局面別・期間別の結果を併記する。

さらに、fee・funding・slippage、見送り率、confidence区間の成績、曖昧決済率、API障害率、データ欠損率、注文拒否率、Reviewerの変更受理率を表示する。

合格閾値はDevelopmentで定め、候補を選ぶValidationの評価前に固定する。最低取引件数、データ充足率、最大drawdown、許容障害率、曖昧率、単純ルールとの差、複数期間と独立試行の安定性を含める。件数不足は「判定不能」であり、ゼロ損益の合格ではない。

独立試行は事前に回数を決め、平均・中央値・分散・最悪結果を報告する。候補数と落選した試行も残す。時間的に近い取引は独立とは限らないため、不確実性は日などのまとまりを保った再集計でも確認する。

### 13.3 エラーと再開

見送り、API失敗、データ不足、Risk拒否、約定拒否は別の状態として記録する。全scheduled slotが一意な最終状態を持つようにする。

運用結果には障害による未発注も反映した実際の口座推移を示す。一方、欠測が多いrunを完全な候補比較とみなさず、事前の充足条件で不完全と判定する。障害slotを消して残った成功分だけを全期間の成績と呼ばない。

Historicalの再開は、事前に決めた再試行規則に従い、同じ時点・入力・状態から未完了分だけを処理する。Reviewerの集計も含めて状態を復元し、成功済み判断・注文・reviewを重複実行しない。Liveで過ぎたslotへ後追い発注しない。

cacheには、完全な入力、strategy、prompt、モデルと生成設定、関数schema、特徴量version、trial IDなどのhashを含める。独立試行間で同じ回答を使い回さない。実応答と失敗も保存し、LLMの完全な再生成可能性と、保存応答を使うReplayの再現性を区別する。

## 14. 実装順序と完了条件

本書の追加PRは文書のみとし、次の実装は別PRへ分ける。

| 段階 | 実装するもの | 完了条件 |
|---|---|---|
| 0 | 既存データinventory、1分足取得条件、共通特徴量仕様 | 必須データ・不足・単位・期間が一覧化される |
| 1 | Trading/Model/Live/Research設定の分離、仮想時計 | walletやdummy秘密鍵なしで研究用serviceを組める。Testnet限定制約は維持 |
| 2 | Dataset、manifest、available_at、共通features | 未来足と欠損を検知し、同じ正規化足なら両Adapterから同じ特徴量が出る |
| 3 | Execution Simulatorと仮想口座 | 手計算fixtureでfee・funding・Entry・Exit・曖昧判定が一致 |
| 4 | HistoricalReplayExchange、ReplayRunner、判断と発注の分離 | Fake Traderによる1日Replayが決定論的に再現できる |
| 5 | 単純ルール、指標、manifest、cache | APIなしで比較・再集計でき、二重実行しない |
| 6 | Traderの共通入力、Reviewer OFFの点評価と連続Replay | 独立判断と口座処理が分離され、候補差分が追える |
| 7 | strategy v2、日次Reviewer、7日/30日集計、見送り仮想評価 | 未来根拠・不正patchを拒否し、OFF/ONを同条件で比較できる |
| 8 | Walk-forward、候補固定、Sealed Test | 人手調整と運転中更新を区別し、Testの誤再利用を拒否できる |
| 9 | Dry-run、Testnet Canary、Forward、遅延計測 | 注文・保護・cleanupの安全性とSimulatorとの差を確認できる |

最初のMVPは、小さな1分足fixture、共通特徴量、Fake Trader、保守的Simulator、Risk Engine、SQLite、損益レポート、単純ルールまでとする。実LLMの大量呼び出しとReviewerは後から追加する。

現行の`runner.py`には実時間参照、サイクル前cleanup、口座とrecent tradesを含むTrader contextがある。Historicalへそのまま移植せず、共通の判断・risk・発注処理を保ちながら時間と状態の扱いを整理する。日次損失はUTC日次で集計し、run全体の累積損失と混同しない。

## 15. 必須テスト

- 未確定足、未来available_at、欠損1分足を検出する。
- return、volatility、ATR、volume z-scoreの計算と単位を固定fixtureで確認する。
- Traderの入力と関数にaccount・recent closed trades・未来データがない。
- 同じ保存回答で、Batch経由と逐次判断経由のReplay結果が一致する。
- LONG/SHORTの価格方向、feeの二重控除防止、funding境界を確認する。
- 両TP/SL到達、窓開け、遅延Entry、max hold、残positionの新規拒否を確認する。
- size上限、日次損失、drawdown、precision、部分約定・cleanupの安全性を維持する。
- ReviewerがUTC日次で1回のみ動き、確定していない取引・仮想結果を参照しない。
- 件数不足、過大なstrategy、許可外変更、親version不一致を拒否する。
- confidence補正が一度だけ適用される。
- split境界でpositionと評価対象結果がまたがず、strategyと集計状態が指定どおり引き継がれる。
- 中断再開でdecision、注文、review、fundingが重複しない。
- 仮想取引の利益が実口座へ加算されない。
- ledgerから損益を再計算でき、全slotの状態とmanifestのhashが一致する。
- Locked Testの通常コマンドからの参照と、消費済みTestの規則外再実行を拒否する。

## 16. Hyperliquidへ進むときの判断

BinanceのLocked Test後は、現在市場のDry-run、少数のTestnet Canary、固定候補のForwardへ進む。Testnetの主目的は、注文・保護・取消・cleanup・遅延・実約定の検証である。Testnetの利益をMainnetの利益の証明にしない。

市場観測時刻、LLM要求と応答、注文送信、応答、fill、保護注文の確認時刻を記録し、Simulatorのコスト・遅延仮定と比較する。

Hyperliquidの価格・出来高による将来の取引機会は、蓄積したHyperliquidデータのReplayまたは別途用意する未発注の仮想売買で確認する。既存dry-runに仮想約定機能があるとはみなさない。市場観測のnetworkと注文先のnetworkを明示し、異なるものを組み合わせた場合は独立の実験条件として扱う。

Mainnetへの注文や実資金運用は本方針の対象外である。Hyperliquidの履歴が不足している間は「Binance上の候補」「Hyperliquidでの動作確認」「Hyperliquid市場での収益検証」を分けて報告する。

Forwardで得た情報を使ってpromptやSimulatorを変更した場合は、新versionとしてDevelopmentへ戻る。同じForward期間を、変更後候補の未見評価として再利用しない。

## 17. まとめ

今回の中心は、次の役割分担である。

> Traderは、その時点の市場情報とstrategyから判断する。Reviewerは1日1回、確定結果からstrategyを更新する。Risk Engineは資金と安全を守り、Execution Simulatorは固定した仮定で取引を再現する。

初期の共通入力は、BinanceとHyperliquidの確定1分足から作るreturn、volatility、ATR、出来高の比較に絞る。板・OI・fundingなどを過去データにないまま足さない。fundingなどの費用は別途正しく扱う。

`strategy.json`は市場の読み方を残す小さな学習領域とし、既存の言葉と項目を保ちながら、記憶量・更新根拠・変更可能範囲を明確にする。

点評価で候補を絞り、連続Replay、Reviewer OFF/ON、Walk-forward、Locked Test、将来のHyperliquid検証へ進む。この順序で「説明がもっともらしいか」ではなく、「費用控除後の判断と学習に再現できる価値があるか」を確認する。
