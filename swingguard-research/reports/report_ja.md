> 公開用の軽量報告。CSV/ZIP、モデルweight、cache、全取引/資産台帳、ローカル環境記録はGitに含めない。詳細成果物はREADMEの再実行で生成する。原論文の完全再現ではなく価格適応版で、Gemini/LLM呼出は0件。

# SwingGuard-inspired price-only adaptation

日次提案と5分リスク制御のオフライン比較。原典の完全再現ではない。

原典はGPT-4oとニュース/onchain/macro等を使用するが、本実験はBTC/ETHの価格特徴量と明示的なルールを使用する。数値閾値、stop/TP、復帰条件、offline ruleは適応の事前仮定であり、評価結果を見た最適化は行っていない。

## 比較の意味

pre_riskは当時の自口座のstate/DD/日次損失/市場下落を日次providerに渡し、生成後にも同一controllerを通す。post_onlyは生成前にriskを渡さず、生成後だけ制御する。uncontrolledは4状態制御を無効にするが、共通の固定SL/TPを維持する。buy_holdは初日に50/50の購入指示を出し、SL/TP・日次リバランスなしで期末に売却する。

offline providerは通常時に20日平均と正の7日momentum、非NORMAL時に60日平均と1.5%超の7日momentumを要求する。60日平均への置換が常に厳しい条件となるわけではなく、riskに応じた別のtrend条件である。pre/postは自口座riskの閉ループ比較であり、同一risk系列の提示順だけを変えた実験ではない。LLMへの事前risk入力効果を立証しない。
このrunのprovider=rule、API送信件数=0。

## データと執行

Binance現物公開5分OHLCV、2024-06-01〜2025-08-01未満。先行60日をwarmupに使用。全月ZIPの配布SHA-256と正規化CSV hashを保存。ms/us、OHLC、重複、欠損、順序、境界、銘柄間一致を検証し、欠損補間はしない。

日次UTC境界に直前まで確定した足だけで提案する。数量は直前終値と当時資産で固定し、次の利用可能な5分始値に片道10bp commission＋5bp slippageで約定。Gemini遅延は到着後最初の始値まで繰り越す。共同weightをmax(1,合計)で正規化し、買付資金不足は比例縮小する。現金・端数量のspot相当、空売り・借入・perp Funding・清算はない。

共通SL8%、TP16%。gap stopは不利な始値、gap TPはTP価格、同足両到達はSL優先。intrabar時刻は不明。保護決済後はその後の正常な日次proposalまで再購入せず、待機中のstopを古い要求の応答で解除しない。期末は最終確定終値で全armに同じ売却費用を課す。

v2では新規注文期限24hを維持し、日次更新中だけ旧建玉を最大15分保護する（最大staleness24h15m）。猶予中は買い増し/再購入禁止で、SL/TP/risk縮小は有効。新提案の許容遅延も15分、受信後最初のopenで切替。既知の不正応答は受信時flat、無応答/遅すぎる応答は固定deadlineでflat。課金結果不明等でproviderが停止したrunは中断し成功結果を公開しない。15分は運用猶予の事前仮定で、成績から選ばない。

SL/TPの基準は初回fill価格。追加購入時はSLをmax(従来stop,新平均取得価格×0.92)として緩めず、TPは最初の建玉fill価格×1.16に固定する。したがって追加後の平均取得価格から16%上のTPとは限らない。部分売却でも価格アンカーを維持し、flat後の新規建玉で更新する。日次proposalの更新だけでは建玉の保有時間やSL/TPをリセットしない。固定の最大保有時間は設けず、見送り/縮小/stop/TP/risk/期末で決済する。

## リスクの事前仮定

NORMAL/ALERT/SURVIVAL/RECOVERYの配分上限は1/.5/0/段階.25,.5,.75,1。ALERTはepisode DD4%、当日損失2%、最大銘柄1h下落3%のいずれか。SURVIVALは12%/6%/8%で次openに全決済。最低12hの待機と静穏条件で復帰開始、静穏な24hごとに段階復帰する。復帰中3%再下落などは再停止。

復帰完了時にcontrollerのepisode高値を更新するが、成績DDの全期間高値はリセットしない。したがって12%は全期間DDの保証ではない。API costを除く時価資産をcontrollerに使用する。

v2では最新確定足をその足が属する日の基準で判定してから新日基準を更新する。00:00のdaily_loss/completed_day_lossは直前完了日、current_day_lossは新当日0。境界でcontrollerを二重更新せず、前日最後の5分足の損失を消さない。exposure drift許容0.5ポイントと次open補正のため、capは足内/連続時間の硬上限ではない。

## 結果

利益・費用はUSDT、DDは5分時価資産の全期間高値比、SharpeはUTC日次リターン・365日年率化・無リスク金利0。turnoverは片道約定額の絶対値合計/初期資産であり、往復でさらに倍しない。fill数とflat-to-flat完結保有数を分ける。exposureは5分時点の平均gross/equity。

### paper_period: 2024-08-01〜2025-02-01未満

| arm | return % | max DD % | daily Sharpe | turnover x | fills | fee | slippage | exposure % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pre_risk | 3.158 | 10.424 | 0.389 | 34.296 | 263 | 342.962 | 171.481 | 27.42 |
| post_only | -5.101 | 15.893 | -0.316 | 35.413 | 309 | 354.132 | 177.066 | 31.26 |
| uncontrolled | -2.453 | 22.969 | 0.012 | 28.812 | 191 | 288.118 | 144.059 | 44.04 |
| buy_hold | 29.911 | 28.484 | 1.213 | 2.299 | 4 | 22.994 | 11.498 | 100.00 |

pre/postの日次weightが異なる日数: 31。各armの状態件数、絶対利益、保有完結数、ベンチマークとの差はsummary.jsonに記録した。

### chronological_followup: 2025-02-01〜2025-08-01未満

| arm | return % | max DD % | daily Sharpe | turnover x | fills | fee | slippage | exposure % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pre_risk | 14.058 | 12.326 | 1.511 | 34.258 | 217 | 342.585 | 171.293 | 25.04 |
| post_only | 3.196 | 13.664 | 0.392 | 34.938 | 278 | 349.384 | 174.692 | 30.27 |
| uncontrolled | 17.577 | 17.475 | 1.209 | 29.788 | 186 | 297.884 | 148.942 | 42.79 |
| buy_hold | 12.191 | 42.701 | 0.689 | 2.122 | 4 | 21.220 | 10.611 | 100.00 |

pre/postの日次weightが異なる日数: 43。各armの状態件数、絶対利益、保有完結数、ベンチマークとの差はsummary.jsonに記録した。

## Gemini と再実行

全365日、3armで1095提案。1回でBTC/ETH両方のweightsを返す。post/uncontrolledの同一入力cacheを共有すれば、365日×2種の入力でGemini実リクエストは最大730件（銘柄数2をさらに掛けない）。既存repoの3.5 Flashは非推奨で3.6へ自動転送されるため、新adapter候補を gemini-3.6-flash とした。キー・SDK・アカウントの利用可能モデルとquotaはreadiness記録で別途確認する。

2026-10-16確認の公開Standard料金 input $0.75/output $3.75 per million （2026-12-31まで）を使い、入力上限10,000 bytesを同数tokenとして上限512 output tokensを足す保守的試算は $6.87660、1件$0.05の予約枠合計は $36.50。無料枠は公開表にあるが、アカウントの利用可否・quotaは未確認である。

$36.50は730件すべてが未確定の仮想的な予約額合計であり、前払い・予想請求額ではない。逐次実行では1件ごとの$0.05を実token×固定単価の確定費用へ置き換え、確定済み費用＋未確定予約＋次の予約が指定予算以内のときだけ送信する。指定上限は10 USD、SDK attemptは1回、結果不明・usage不足は停止して自動再送しない。同時writerはOS lockで拒否する。

[公開料金](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.6-flash)、[3.5の非推奨と転送](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash)。公開候補は3.6 Flash、3.8 Flash、3.5 Flash-Lite。アカウントでのmodels.list照合はキー未確認のため未実施。

v2 adapterはrequest/受信応答/usage/latency/単価/費用/検証済みproposalを同一journalレコードに保存し、受信保存後の中断は再送せず精算・cache再構築で復旧する。保存前の結果不明は予約を残して停止。totalと内訳を照合し、thinking欠落を0と確定できない場合は停止し、別hashも未確定課金を迂回できない。異常usageは予約以上の観測保守額を残す。単一attempt、構成モデル/単価/期限も照合する。

費用ガードは同じjournalのローカル管理であり、別cache/他アプリ/価格変更/供給側のtoken制限違反を含む実invoiceの上限を保証しない。受信保存前の電源断、ディスク破損、未知のprovider不整合から無条件に自動復旧する保証もない。v1は保存し、修正前後の全arm差をresults/comparison_v1_v2に記録する。

## 限界

価格だけのルールによる探索的な適応で、LLMの成績ではない。後続半年はこの実験の閾値調整には未使用だが、既存repoの過去研究やモデル学習に未使用とは保証しない。将来Geminiを使う歴史検証ではlive retrievalを禁止しても、後年の値動きの記憶による汚染を排除できない。

5分OHLCでは足内の到達順序、板・市場インパクト、正確なstop時刻、実際の流動性、取引最小単位、税・保管費を再現しない。固定費用は実取引所条件の保証ではない。Binance現物相当の結果をHyperliquidやperpの収益性と解釈しない。

出典: https://arxiv.org/html/2610.12988v1 （非公開条件は本実験の仮定。）
