"""Checksum-verified spot archives, reusing the repository's Binance helpers."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

BAR_MS = 300_000
DAY_MS = 86_400_000


def date_ms(text):
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


@dataclass(frozen=True)
class Bar:
    open_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @property
    def available_ms(self):
        return self.open_ms + BAR_MS


def timestamp_scale(raw):
    if 1_000_000_000_000 <= raw < 100_000_000_000_000:
        return 1
    if 100_000_000_000_000 <= raw < 10_000_000_000_000_000:
        return 1000
    raise ValueError("timestamp must be Unix milliseconds or microseconds, never seconds")


def parse_archive_row(row):
    raw_open, raw_close = int(row[0]), int(row[6])
    scale = timestamp_scale(raw_open)
    if raw_open % scale or (raw_close + 1) % scale:
        raise ValueError("unaligned timestamp unit")
    start = raw_open // scale
    if start % BAR_MS or (raw_close + 1) // scale != start + BAR_MS:
        raise ValueError("invalid five-minute close/availability")
    bar = Bar(start, *(Decimal(row[i]) for i in range(1, 6)))
    validate_bar(bar)
    return bar


def validate_bar(bar):
    prices = (bar.open, bar.high, bar.low, bar.close)
    if not all(p.is_finite() and p > 0 for p in prices):
        raise ValueError("invalid OHLC")
    if bar.high < max(prices) or bar.low > min(prices):
        raise ValueError("inconsistent OHLC")
    if not bar.volume.is_finite() or bar.volume < 0:
        raise ValueError("invalid volume")


def validate_bars(bars, start, end):
    if len(bars) != (end - start) // BAR_MS:
        raise ValueError("missing/extra bars")
    for index, bar in enumerate(bars):
        validate_bar(bar)
        if bar.open_ms != start + index * BAR_MS:
            raise ValueError("gap, duplicate, out-of-order or misaligned bar")


def months(start, end):
    cursor = datetime.fromtimestamp(start / 1000, timezone.utc).replace(day=1)
    while int(cursor.timestamp() * 1000) < end:
        yield cursor.strftime("%Y-%m")
        cursor = cursor.replace(year=cursor.year + (cursor.month == 12), month=cursor.month % 12 + 1)


def legacy_helpers():
    path = Path(__file__).resolve().parents[3] / "binance-btcusdt-futures-research/download_binance_klines.py"
    spec = importlib.util.spec_from_file_location("swingguard_binance_helpers", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def download(config, root):
    helpers = legacy_helpers()
    start, end = date_ms(config["data_start"]), date_ms(config["data_end_exclusive"])
    records, datasets = [], {}
    for symbol in config["symbols"]:
        bars, units = [], set()
        for month in months(start, end):
            name = f"{symbol}-5m-{month}.zip"
            url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/5m/{name}"
            target = root / "raw" / name
            checksum = target.with_suffix(".zip.CHECKSUM")
            target.parent.mkdir(parents=True, exist_ok=True)
            valid = target.exists() and checksum.exists() and sha(target) == helpers.parse_checksum(checksum.read_bytes())
            if not valid:
                checksum_payload = helpers.download_bytes(url + ".CHECKSUM")
                expected = helpers.parse_checksum(checksum_payload)
                payload = helpers.download_bytes(url)
                if hashlib.sha256(payload).hexdigest() != expected:
                    raise ValueError(f"archive checksum mismatch: {name}")
                part = target.with_suffix(".zip.partial")
                part.write_bytes(payload)
                part.replace(target)
                checksum.write_bytes(checksum_payload)
            rows = helpers.read_kline_rows(target)
            for row in rows:
                units.add("us" if timestamp_scale(int(row[0])) == 1000 else "ms")
                bar = parse_archive_row(row)
                if start <= bar.open_ms < end:
                    bars.append(bar)
            records.append({"symbol": symbol, "month": month, "url": url, "sha256": sha(target)})
            print(f"verified {name}", flush=True)
        validate_bars(bars, start, end)
        path = root / f"{symbol}-5m.csv"
        temp = path.with_suffix(".csv.partial")
        with temp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["open_ms", "available_ms", "open", "high", "low", "close", "volume"])
            writer.writerows((b.open_ms, b.available_ms, b.open, b.high, b.low, b.close, b.volume) for b in bars)
        temp.replace(path)
        datasets[symbol] = {"path": path.name, "sha256": sha(path), "rows": len(bars),
                            "start_ms": start, "end_ms": end, "source_timestamp_units": sorted(units)}
    manifest = {"schema_version": 1, "venue": "binance_spot_public", "interval_ms": BAR_MS,
                "start_ms": start, "end_ms": end, "datasets": datasets, "archives": records,
                "validation": "complete; checksums, units, OHLC, continuity, duplicates and boundaries verified"}
    write_json(root / "manifest.json", manifest)
    return manifest


def load(config, root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    start, end = date_ms(config["data_start"]), date_ms(config["data_end_exclusive"])
    if (manifest["venue"], manifest["interval_ms"], manifest["start_ms"], manifest["end_ms"]) != (
            "binance_spot_public", BAR_MS, start, end):
        raise ValueError("wrong dataset identity or period")
    result = {}
    for symbol in config["symbols"]:
        entry = manifest["datasets"][symbol]
        path = root / entry["path"]
        if not path.resolve().is_relative_to(root.resolve()) or sha(path) != entry["sha256"]:
            raise ValueError("input hash/path mismatch")
        bars = []
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                bar = Bar(int(row["open_ms"]), *(Decimal(row[k]) for k in ("open", "high", "low", "close", "volume")))
                if int(row["available_ms"]) != bar.available_ms:
                    raise ValueError("unavailable candle timestamp")
                bars.append(bar)
        validate_bars(bars, start, end)
        if len(bars) != entry["rows"]:
            raise ValueError("manifest row count mismatch")
        result[symbol] = bars
    return result, manifest
