> 公開用の軽量報告。CSV/ZIP、モデルweight、cache、全取引/資産台帳、ローカル環境記録はGitに含めない。詳細成果物はREADMEの再実行で生成する。原論文の完全再現ではなく価格適応版で、Gemini/LLM呼出は0件。

# 満了openのgap決済規約: レビュー修正と固定weight再評価

独立レビューのP2を修正した。48h満了openにも既存のgap stop/TPを優先適用し、その後にmax_hold決済とする。上方gapはTP価格に制限、下方gapは不利なopenで売る。満了足のhigh/lowは参照しない。

変更対象はresearch.pyのmake_episodeだけ。学習関数・oracle関数・scaler・config・threshold・各パラメータは変更せず、optimizer/学習は実行していない。15個の保存weightと各training.jsonをそのままコピーし、weight_hashes.jsonに旧新hashを保存した。warmの追加MLP学習とBC anchorがあるため、rawとの差を初期重みだけの効果とは呼ばない。

## 境界再現とテスト

entry raw=100、48h満了open=120、初期slot50,000の合成例では、旧約定120からTP108.054へ修正した。旧期末cash 59820.269685337、新53865.161838162。これは合成例であり実データの水増しを意味しない。synthetic_gap_reproduction.jsonに費用と数量を保存した。

8境界テストのうち旧ソースで5件が期待通り失敗し、新ソースでは既存17件を含む全25件が通過。満了上方/下方gap、TP/SLちょうどのopen、保護価格内の満了、満了足の後続high/low無視、前の足のstop優先、倍costのfillアンカーを検証した。旧失敗log、新成功log、ソースとテストのbefore/after snapshot、unified diffを保存した。

## 既存結果への影響

train/val/testの全406episodeを通常・倍cost・5m遅延の3条件で監査し、計1218pathのforced exit/candidate outcomeに変更は0/0件。train246episodeのoracleラベル変更は0件。新たな教師を学習更新に使っていない。

保存15モデルと固定2ルールで全100runを再評価した。8000取引、2,413,475資産行を会計照合し、全方式の共通entryと現金待機を確認。旧metrics/trades/equityは全run byte一致、全数値指標の最大絶対差は0.0。元cpu-v2の336成果物hashとmanifestを前後で確認し、旧出力は一切上書きしていない。今回保存結果の利益/DD/Sharpe/費用・結論は変わらない。

| 方策 | 通常test net% mean | seed SD |
| --- | ---: | ---: |
| fixed_12h | -17.642477 | 0.000000 |
| fixed_max | 11.955854 | 0.000000 |
| mlp | -9.191475 | 5.712374 |
| mlp_rl | -9.317146 | 5.705254 |
| raw_rl | -10.771208 | 4.326468 |

MLP→ACの改善・統計優位は確認されないという結論を維持する。これはSPX short-straddle論文の完全再現ではなく、BTC/ETH現物相当の小規模価格適応である。今回の修正を理由にtestを見た追加学習・parameter探索は行っていない。
