"""Strict local Binance candle/Funding inputs for offline minute replay."""
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .models import MarketTick


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_replay_inputs(candles: Path, funding: Path, start: int, end: int):
    """Require full minute and 8-hour coverage; never fill missing observations."""
    if start % 86_400_000 or end % 86_400_000 or end <= start:
        raise ValueError("replay bounds must be ordered UTC midnights")
    ticks = []
    with candles.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line, parse_float=Decimal)
            opened = int(row["open_time_ms"])
            if not start <= opened < end:
                continue
            closed = int(row["close_exclusive_ms"])
            if (row["venue"] != "binance_usdm_public" or row["symbol"] != "BTCUSDT"
                    or closed != opened + 60_000 or int(row["available_at_ms"]) != closed):
                raise ValueError("wrong market, interval, or candle availability")
            ticks.append(MarketTick(closed, *(Decimal(str(row[key])) for key in ("open", "high", "low", "close"))))
    if [tick.timestamp_ms for tick in ticks] != list(range(start + 60_000, end + 1, 60_000)):
        raise ValueError("minute coverage has missing, duplicate, or unordered rows")
    rates = {}
    boundaries = []
    source_events = []
    with funding.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            timestamp = int(row["calc_time"])
            # This historical source sometimes timestamps settlement 1 ms late.
            boundary = timestamp // 28_800_000 * 28_800_000
            if not start <= boundary < end:
                continue
            if timestamp - boundary > 1000 or int(row["funding_interval_hours"]) != 8:
                raise ValueError("unsupported Funding schedule")
            rate = Decimal(row["last_funding_rate"])
            if not rate.is_finite():
                raise ValueError("invalid Funding rate")
            boundaries.append(boundary)
            # Never apply an observed settlement before its source timestamp.
            applied = max(start + 60_000, (timestamp + 59_999) // 60_000 * 60_000)
            if applied in rates:
                raise ValueError("duplicate Funding settlement")
            rates[applied] = rate
            source_events.append({"source_timestamp_ms": timestamp, "applied_timestamp_ms": applied, "rate": str(rate)})
    if boundaries != list(range(start, end, 28_800_000)):
        raise ValueError("Funding coverage has missing, duplicate, or unordered rows")
    return ticks, rates, {
        "venue": "binance_usdm_public", "symbol": "BTCUSDT",
        "start_ms": start, "end_exclusive_ms": end,
        "candle_count": len(ticks), "funding_count": len(rates),
        "candles_sha256": file_hash(candles), "funding_sha256": file_hash(funding),
        "funding_events": source_events,
        "funding_price_model": "minute open proxy; source event rounded forward to minute close",
    }
