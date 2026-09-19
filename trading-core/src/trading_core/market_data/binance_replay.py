"""Public Binance data capture with exact Funding settlement prices; no credentials."""
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

from .models import FundingSettlement, MarketTick


def capture(start: int, end: int, output: Path):
    if output.exists():
        return load(output, start, end)
    if start % 86_400_000 or end % 86_400_000 or end <= start:
        raise ValueError("expected UTC day bounds")
    opener = build_opener(ProxyHandler({}))
    def get(endpoint, **params):
        url = "https://fapi.binance.com/fapi/v1/" + endpoint + "?" + urlencode(params)
        with opener.open(url, timeout=30) as response:
            return json.load(response)
    candles = []
    cursor = start
    while cursor < end:
        rows = get("klines", symbol="BTCUSDT", interval="1m", startTime=cursor, endTime=end-1, limit=1000)
        if not rows or int(rows[-1][0]) < cursor:
            raise ValueError("candle data unavailable")
        candles.extend(rows)
        cursor = int(rows[-1][0]) + 60_000
    funding = get("fundingRate", symbol="BTCUSDT", startTime=start, endTime=end-1, limit=1000)
    data = {"schema_version": 1, "venue": "binance_usdm_public", "symbol": "BTCUSDT",
            "start_ms": start, "end_ms": end, "candles": candles, "funding": funding}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial.json")
    temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    result = load(temporary, start, end)
    temporary.replace(output)
    return result


def load(path: Path, start: int, end: int):
    raw = path.read_bytes()
    data = json.loads(raw)
    if (data["venue"], data["symbol"], data["start_ms"], data["end_ms"]) != ("binance_usdm_public", "BTCUSDT", start, end):
        raise ValueError("input identity or period mismatch")
    rows = data["candles"]
    if [int(row[0]) for row in rows] != list(range(start, end, 60_000)):
        raise ValueError("incomplete or duplicate candles")
    ticks = []
    for row in rows:
        if int(row[6]) + 1 != int(row[0]) + 60_000:
            raise ValueError("wrong candle close time")
        ticks.append(MarketTick(int(row[0])+60_000, *(Decimal(row[i]) for i in (1,2,3,4))))
    boundaries, funding = [], {}
    for row in data["funding"]:
        if row["symbol"] != "BTCUSDT":
            raise ValueError("wrong Funding symbol")
        timestamp = int(row["fundingTime"])
        boundary = timestamp // 28_800_000 * 28_800_000
        if not start <= timestamp < end or timestamp - boundary > 1000:
            raise ValueError("unsupported Funding schedule")
        boundaries.append(boundary)
        # At the initial boundary the account is flat; there is no preceding bar.
        if timestamp == start:
            continue
        applied_tick = (timestamp + 59_999) // 60_000 * 60_000
        if applied_tick in funding:
            raise ValueError("duplicate Funding")
        funding[applied_tick] = FundingSettlement(timestamp, Decimal(row["fundingRate"]), Decimal(row["markPrice"]))
    if boundaries != list(range(start, end, 28_800_000)):
        raise ValueError("incomplete Funding")
    return ticks, funding, {"sha256": hashlib.sha256(raw).hexdigest(), "candles": len(ticks),
                           "funding": len(boundaries), "start_ms": start, "end_ms": end}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.start, args.end, args.output)[2]))
