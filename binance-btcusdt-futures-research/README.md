# Binance BTCUSDT USDⓈ-M Futures Research Data

Binance公式の公開アーカイブから、`BTCUSDT` 無期限先物の確定済み直近365 UTC日を取得する再現用ディレクトリです。`data/` には日足・1時間足・15分足の結合済みCSVが置かれますが、再生成可能な大容量データのためGitには追加しません。

## 取得

リポジトリのルートから次を実行します。

```powershell
python .\binance-btcusdt-futures-research\download_binance_klines.py --end-date YYYY-MM-DD
```

`--end-date` は範囲に含めないUTC日です。たとえば `2026-08-17` は、2025-08-17 00:00:00 UTCから2026-08-16 23:59:59.999 UTCまでを取得します。月の全期間が対象なら月次ZIP、それ以外は日次ZIPを使い、各ZIPは公式 `.CHECKSUM` とSHA-256照合してから結合します。

出力は次の通りです。

- `data/BTCUSDT-1d-365d.csv` — 365行
- `data/BTCUSDT-1h-365d.csv` — 8,760行
- `data/BTCUSDT-15m-365d.csv` — 35,040行
- `metadata/fetch-YYYY-MM-DD.json` — ソースURL、SHA-256、行数、取得時刻

既存CSVを再ダウンロードせずに検証するには、同じ `--end-date` に `--verify-only` を追加します。

```powershell
python .\binance-btcusdt-futures-research\download_binance_klines.py --end-date YYYY-MM-DD --verify-only
```

CSVは `open_time_utc` と、元のミリ秒開始時刻を含むBinance Klineの12列を含みます。開始時刻はUTC昇順かつ重複なしで、全期間の足が連続することを検証します。
