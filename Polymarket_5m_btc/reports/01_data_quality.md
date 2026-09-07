# Data quality and implementation verification

更新日: 2026-09-07

## 実行環境

- Interpreter: `Polymarket_5m_btc/.venv/Scripts/python.exe`
- Python: 3.13.1
- Dependency management: `pip` with `requirements.txt` and `requirements-dev.txt`
- `polymarket-client`: 0.9.0

## オフライン検証

- pytest: 29 passed
- ruff: passed (`src`, `tests`)
- pyright: 0 errors, 0 warnings, 0 informations
- fixture: JSONL staging、ZSTD Parquet、quality JSON、lead-lag JSONを生成

## 公開データ smoke

- PMXT coverage (2026-08-10 00:00--03:00 UTC): 00時のみ存在、01時・02時は欠損。ファイル本体は取得していない。
- 5秒 collector: Binance 33行、Coinbase 129行、Hyperliquid 57行、Polymarket 3,605行。全て `valid: true`、duplicate sequence 0、timestamp inversion 0。
- Polymarket market master: 3 markets。
- 別10秒 Chainlink smoke: 14行、`valid: true`。
- 実受信データの短時間 lead-lag: `insufficient_data`（external shock 0件）。これは失敗ではなく、結論に必要なサンプル量がまだないことを示す。

## 解釈上の制約

fixtureのlead-lag結果は `exploratory` であり、実市場の統計的有意性・PnL・約定可能性を示さない。過去アーカイブに自分の受信時刻がない場合、receive-time分析は `insufficient_data` とする。prospective collectorを実行して実データを蓄積するまで、売買判断や収益性の結論は出さない。

## 次の検証

1. boundedなPMXT coverageを取得し、欠損hourをmanifestに保存する。
2. Binance/Coinbase/Hyperliquid/Polymarket/Chainlinkを24/7で保存する。
3. source timestampとlocal receive timestampを分けて、receive-time lead-lagを再計算する。
