# Gemini 3.6 Flash 移行・検証記録（2026-10-11）

調査基点: main `44799022bcc4ed141ad0977d1fa5ee217c5d838d`。

## 移行範囲

| 箇所 | 変更 |
| --- | --- |
| llm-position-management-stg | Trader既定値 / .env.example / READMEをgemini-3.6-flashへ変更。Reviewerのgemini-3.8-flashは維持 |
| llm-position-management | GeminiPolicyの固定モデルと料金・予算fingerprint・comparisonのモデル情報を変更。モデルと単価定数を共用 |
| hyperliquid-ai-trader研究Batch | 新しい3.6 Flash専用pilot設定・experiment IDを追加。CLIのモデル制約・Batch予約単価・README例を更新 |
| hyperliquid-ai-trader運用 | Trader既定値gemini-3.5-flash-lite、Reviewerのgemini-3.6-flash、既存fallback指定は変更しない |

[Googleの非推奨通知](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash)は3.5 Flash→3.6 Flash自動転送を説明する。[3.6仕様](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)はStructured Outputs・Function Calling・Batchをサポートする。3.5 Flash-Liteは別モデルであり今回の変更対象ではない。

過去のSTAGE2_VALIDATION、完了済み実験記録、旧 `llm_pilot_v001_gemini_35_flash_minimal.json` は証跡として保存する。旧pilotは新送信に使わず、新しい `llm_pilot_v001_gemini_36_flash_minimal.json` を使う。旧モデルを含む一部テストは、旧台帳の混入拒否や異なるreturned modelの検証のため意図的に残す。

## Structured Output・期限

BotはPydantic AIを使用していない。既存google-genai `>=1,<2`で解決された1.75.0を使い、実SDKのHTTPをMockTransportで捕捉した。3.6 Flash宛て要求のJSON Schema・application/json・thinking=low・出力上限・単一送信と、hold / set_target / flatの判断を既存risk plannerが受け入れることを確認した。モデル変更のためのSDK更新は行っていない。

stgのTrader 60秒 / Reviewer 180秒 / cycle 270秒と保存余裕20秒を維持する。SDKはattempts=1、tool自動実行無効。応答が期限を超えた場合の破棄、通信失敗時の予約保持、同じrequest IDの有料再送禁止を検証した。llm-position-managementの30秒HTTP期限、minimal thinking、512最大出力も維持。判断schema・参照や数値の検証・SLリスク上限は変更しない。実レイテンシは未確認。

Batch結果にはSDKの実際のmodel_versionを記録し、提供されない場合のみ要求モデルへfallbackする。要求aliasを常にreturned modelとして記録していた処理を修正した。

## 料金と予算

[公式料金表](https://ai.google.dev/gemini-api/docs/pricing)を2026-10-11に確認。Gemini Developer APIのUSD / 100万token。出力はthinkingを含む。

| 3.6 Flash | 2026-12-31まで入力 / 出力 | 2027-01-01から入力 / 出力 |
| --- | --- | --- |
| Standard | 0.75 / 3.75 | 1.50 / 7.50 |
| Batch | 0.375 / 1.875 | 0.75 / 3.75 |

料金上昇後も過小予約しないよう、stgと同期研究では**Standard 1.50 / 7.50**、Batch予約では**0.75 / 3.75**を保守的な既定値にする。これは2026年の実請求単価そのものではなく、割引期間中は推定・予約が多めになる。stgの既定input 1.50は据置、旧Flash出力9を7.50へ更新する。Reviewerは対象外で既存単価も維持する。stgでは確認済み料金を環境変数で明示override可能。モデルを変更する場合はモデルに対応した単価も必ず設定する。

stgはprompt tokenとcandidates+thoughtsを課金対象に含め、レコードへ適用単価・requested/returned model・config hashを保存する。応答不明では予約を返還しない。Free Tierは明示設定された場合のみ0推定にする。月$20、初期notify_onlyは据置であり**ハード停止ではない**。`LLM_BUDGET_ACTION=stop`なら次の予約で上限を超える呼出をネットワーク前に拒否する。

同期研究は$0.05/要求の保守予約を維持する（入力byte制限10,000＋overheadと最大出力512を新料金で評価しても予約内）。comparisonのUSD上限も維持する。新しいモデル・単価で過去の台帳やキャッシュを再利用せず、別experiment / 出力 / API台帳に分ける。comparison protocolはv3へ上げ、freezeへ単価も含める。旧DBを消去せず、モデル移行前後を同一実験として継続しない。

研究Batchの$0.35・最大出力512・地点数は維持し、将来料金を使うbyte上限と出力上限の予約を計算する。モデル変更でcanonical request hashが変わるので、要求artifactを新experimentで再準備する。過去に送信済みのjob・未解決予約を再送しない。

## 配備・研究再開

1. stgの既存環境の `TRADER_MODEL=gemini-3.5-flash` を3.6 Flashへ変更。`TRADER_INPUT_USD_PER_MILLION=1.5`、`TRADER_OUTPUT_USD_PER_MILLION=7.5`を保守既定値として確認する。
2. `LLM_MONTHLY_BUDGET_USD`とnotify_only / stopを運用意図に合わせて確認する。モデル切替を理由に残予算や不明要求をリセットしない。
3. 新設定でstgを再配備。既存runnerはconfig hashの変化を記録し、建玉・SLを維持して新しい評価segmentを開始する。この挙動をfixtureで検証した。
4. 同期comparisonは新しいoutputとapi-ledgerを用い、canaryからやり直す。旧freeze / budget.dbを上書きしない。
5. Batchは新しい36設定と新experimentのartifactを使用。旧jobは元台帳で照会し、再送しない。今回Batch送信は行っていない。

配備先への接続情報はなく、稼働環境への変更は今回行っていない。

## 回帰検証・制限

Python 3.12、google-genai 1.75.0。trading-coreと対象3パッケージを同じ専用venvへeditable install。各packageをcwdとして `python -m pytest -q` を実行した。

| package | 結果 |
| --- | --- |
| llm-position-management-stg | 39 passed |
| llm-position-management | 46 passed |
| hyperliquid-ai-trader | 186 passed |

stgはschema/SDK HTTP・thinking課金・予算停止・通信失敗/遅延・モデル移行時の状態保持を含む。同期研究は旧台帳拒否・新モデル料金・キャッシュ・判断schemaを含む。hyperliquidは既存の運用Trader/Lite設定・判断parser・FakeExchange・研究Batchを含む。実注文・Testnet注文は**0件**。すべての取引所adapterはfakeで、署名用キーを設定していない。実APIキーもなく、Gemini実API・実応答の品質/遅延・課金精算は未検証。これは収益性や取引成績の検証ではない。
