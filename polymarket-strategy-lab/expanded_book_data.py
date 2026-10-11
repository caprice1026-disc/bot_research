"""Select BTC 5m books from public Pendulum Flow archives, never full hourly dumps.

Run with ../Polymarket_5m_btc/.venv/Scripts/python.exe expanded_book_data.py.
Frozen sample: September 1--30, 2026, 16:00--17:00 UTC each day.
The archive records collector receipt times, not this user's network latency.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".probe-deps"))
# DuckDB's HTTP library reads proxy settings when the native module loads.
for _key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_key, None)
import duckdb

from btc_data import replay_rows


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def cached_json(url: str, path: Path) -> dict:
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("url") != url:
            raise ValueError(f"Cache URL mismatch: {path}")
        return cached
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(4):
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=35) as response:
                record = {"retrieved_at": datetime.now(timezone.utc).isoformat(),
                          "url": url, "status": response.status, "body": json.load(response)}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return record
        except (OSError, ValueError):
            if attempt == 3:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def connect():
    # Explicit public reads avoid stale machine proxy configuration. TLS stays on.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    extensions = ROOT / ".probe-deps/extensions"
    extensions.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"extension_directory": extensions.as_posix(),
                                "custom_user_agent": "Mozilla/5.0"})
    con.execute("LOAD httpfs")
    import certifi
    con.execute("SET ca_cert_file=?", [certifi.where()])
    con.execute("SET enable_server_cert_verification=true")
    con.execute("SET enable_curl_server_cert_verification=true")
    con.execute("SET http_timeout=40")
    con.execute("SET http_retries=2")
    con.execute("SET TimeZone='UTC'")
    return con


def market_metadata(ts: int, cache: Path) -> dict:
    slug = f"btc-updown-5m-{ts}"
    url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({"slug": slug})
    events = cached_json(url, cache / f"{slug}.json")["body"]
    matches = [m for event in events for m in event.get("markets", []) if m.get("slug") == slug]
    if len(matches) != 1:
        raise ValueError(f"Expected one market for {slug}; found {len(matches)}")
    market = matches[0]
    outcomes, tokens = json.loads(market["outcomes"]), json.loads(market["clobTokenIds"])
    if set(outcomes) != {"Up", "Down"} or len(tokens) != 2:
        raise ValueError(f"Not a binary up/down market: {slug}")
    return {"market_id": market["conditionId"], "slug": slug,
            "up_token_id": tokens[outcomes.index("Up")], "down_token_id": tokens[outcomes.index("Down")],
            "window_start_ts": ts, "window_end_ts": ts + 300}


def archive_events(con, parquet: Path):
    query = """SELECT event_type,epoch(timestamp_received),epoch_us(timestamp),asset_id,
        best_bid,best_ask,bids,asks,new_tick_size,sequence
        FROM read_parquet(?) ORDER BY timestamp_received,sequence,event_type"""
    cursor = con.execute(query, [str(parquet)])
    while rows := cursor.fetchmany(1000):
        for kind, received, source, token, bid, ask, bids, asks, tick, sequence in rows:
            payload = {"bids": bids, "asks": asks} if kind == "book" else {"new_tick_size": tick}
            yield {"source": "polymarket", "symbol": str(int.from_bytes(token, "big")),
                   "event_type": kind, "recv_ts": received, "source_event_ts": source,
                   "connection_id": "pendulum:" + parquet.stem,
                   "bid": float(bid) if bid is not None else None,
                   "ask": float(ask) if ask is not None else None,
                   "raw_payload": json.dumps(payload, default=float)}


def binance_bars(start: int, cache: Path):
    """True one-second Binance closes, available only after close + one second."""
    begin, end = start - 120, start + 3600
    cursor, bars, files = begin * 1000, {}, []
    while cursor < end * 1000:
        params = {"symbol": "BTCUSDT", "interval": "1s", "startTime": cursor,
                  "endTime": end * 1000 - 1, "limit": 1000}
        url = "https://data-api.binance.vision/api/v3/klines?" + urllib.parse.urlencode(params)
        path = cache / f"{cursor}.json"
        values = cached_json(url, path)["body"]
        if not isinstance(values, list) or not values:
            break
        for value in values:
            opened, closed, price = int(value[0]), int(value[6]) + 1, float(value[4])
            if not cursor <= opened < end * 1000 or closed - opened != 1000 or price <= 0:
                raise ValueError("Invalid Binance one-second candle")
            bars[opened] = {"source_ts": closed / 1000, "available_ts": closed / 1000 + 1,
                            "price": price}
        files.append({"file": str(path), "sha256": sha256(path), "rows": len(values)})
        following = int(values[-1][0]) + 1000
        if following <= cursor:
            raise ValueError("Binance pagination did not advance")
        cursor = following
    ordered = [bars[key] for key in sorted(bars)]
    return ordered, {"expected_seconds": end - begin, "actual_seconds": len(ordered),
                     "missing_seconds": end - begin - len(ordered), "files": files,
                     "availability_assumption": "One second after the historical kline's close; no historical network receipt timestamp"}


def with_binance(rows, bars):
    availability = [bar["available_ts"] for bar in bars]
    for row in rows:
        index = bisect.bisect_left(availability, row["decision_ts"]) - 1
        row.update({"binance_twap60_price": None, "binance_twap60_source_ts": None,
                    "binance_twap60_available_ts": None})
        if index >= 0:
            bar = bars[index]
            row.update({"binance_price": bar["price"], "binance_source_ts": bar["source_ts"],
                        "binance_recv_ts": bar["available_ts"],
                        "binance_age_seconds": row["decision_ts"] - bar["available_ts"]})
            previous = bars[max(0, index - 59):index + 1]
            if len(previous) == 60 and previous[-1]["source_ts"] - previous[0]["source_ts"] == 59:
                row.update({"binance_twap60_price": sum(b["price"] for b in previous) / 60,
                            "binance_twap60_source_ts": bar["source_ts"],
                            "binance_twap60_available_ts": bar["available_ts"]})
        yield row


def extract_hour(con, day: date, hour: int, output_dir: Path) -> dict:
    stem = f"{day.isoformat()}T{hour:02d}"
    result_path = output_dir / f"{stem}.manifest.json"
    parquet, csv_path = output_dir / f"{stem}.parquet", output_dir / f"{stem}.csv"
    if result_path.exists():
        previous = json.loads(result_path.read_text())
        if (previous.get("status") == "extracted" and previous.get("schema_version") == 2
                and parquet.exists() and csv_path.exists()
                and sha256(parquet) == previous["subset_sha256"] and sha256(csv_path) == previous["csv_sha256"]):
            print(f"reuse {stem} rows={previous['counts']['feature_rows']}", flush=True)
            return previous
    start = int(datetime.combine(day, datetime.min.time(), timezone.utc).timestamp()) + hour * 3600
    with ThreadPoolExecutor(max_workers=6) as executor:
        markets = list(executor.map(lambda ts: market_metadata(ts, output_dir / "metadata"), range(start, start + 3600, 300)))
    path = f"{day.isoformat()}/{hour:02d}/{stem}.parquet"
    source_url = "https://dl.pendulumflow.com/v3/" + path
    manifest_url = f"https://archive.pendulumflow.com/v3/{day.isoformat()}/{hour:02d}/manifest.json"
    source_manifest = cached_json(manifest_url, output_dir / f"{stem}.source.json")["body"]
    conditions = [bytes.fromhex(m["market_id"][2:]) for m in markets]
    partial = parquet.with_suffix(".parquet.part")
    # Every product occupies separate Parquet rowgroups. Filter before download.
    con.execute("""COPY (SELECT event_type,timestamp_received,timestamp,market,asset_id,
            best_bid,best_ask,bids,asks,new_tick_size,sequence
        FROM read_parquet($source) WHERE market IN (SELECT unnest($conditions))
        AND event_type IN ('book','best_bid_ask','tick_size_change'))
        TO $destination (FORMAT PARQUET)""", {"source": source_url, "conditions": conditions, "destination": str(partial)})
    partial.replace(parquet)
    bars, binance_manifest = binance_bars(start, output_dir / "binance")
    counts = Counter()
    csv_partial = csv_path.with_suffix(".csv.part")
    with csv_partial.open("w", encoding="utf-8", newline="") as handle:
        writer = None
        for row in with_binance(replay_rows(archive_events(con, parquet), markets, counts), bars):
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
    if not counts["feature_rows"]:
        raise ValueError(f"No replay data for {stem}")
    csv_partial.replace(csv_path)
    result = {"status": "extracted", "schema_version": 2, "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "hour": stem, "requested_start_ts": start, "requested_end_ts": start + 3600,
              "source_url": source_url, "source_manifest_url": manifest_url,
              "source_full_sha256": source_manifest.get("sha256"), "source_full_bytes": source_manifest.get("bytes"),
              "source_manifest_sha256": sha256(output_dir / f"{stem}.source.json"),
              "subset_sha256": sha256(parquet), "subset_bytes": parquet.stat().st_size,
              "csv_sha256": sha256(csv_path), "markets": markets, "counts": dict(counts),
              "binance": binance_manifest}
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"extracted {stem} rows={counts['feature_rows']} valid_pairs={counts['valid_paired_rows']} subset={parquet.stat().st_size:,}", flush=True)
    return result


def run(start: date, end: date, hour: int, output: Path):
    if not start <= end or not 0 <= hour <= 23:
        raise ValueError("Invalid date interval or UTC hour")
    cache = ROOT / "data/expanded_books"
    cache.mkdir(parents=True, exist_ok=True)
    con, hours, failures = connect(), [], {}
    day = start
    while day <= end:
        try:
            hours.append(extract_hour(con, day, hour, cache))
        except Exception as exc:
            failures[day.isoformat()] = f"{type(exc).__name__}: {exc}"
            print(f"failed {day}: {failures[day.isoformat()]}", flush=True)
        day += timedelta(days=1)
    con.close()
    if hours:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.with_suffix(".csv.part").open("w", encoding="utf-8", newline="") as handle:
            writer = None
            for item in hours:
                with (cache / (item["hour"] + ".csv")).open(encoding="utf-8", newline="") as source:
                    reader = csv.DictReader(source)
                    if writer is None:
                        writer = csv.DictWriter(handle, fieldnames=reader.fieldnames)
                        writer.writeheader()
                    writer.writerows(reader)
        output.with_suffix(".csv.part").replace(output)
    manifest = {"status": "partial" if failures else "extracted", "start_date": start.isoformat(),
                "end_date": end.isoformat(), "utc_hour": hour, "requested_hours": (end - start).days + 1,
                "extracted_hours": len(hours), "market_count": sum(len(x["markets"]) for x in hours),
                "counts": dict(sum((Counter(x["counts"]) for x in hours), Counter())),
                "failures": failures, "output": str(output), "output_sha256": sha256(output) if hours else None,
                "source": "Pendulum Flow public Polymarket v3 archive, CC BY 4.0, https://archive.pendulumflow.com",
                "selection": "All 12 BTC5m windows in fixed UTC hour 16 daily, September 1--30, fixed before results",
                "time_basis": "Archived collector timestamp_received; assumed one additional second to hypothetical bot execution",
                "limitations": ["Only one hour per day, not full-day continuous coverage",
                    "Merged collectors do not preserve any single connection's arrival order",
                    "Price-change deltas omitted; changed BBO invalidates depth until next full snapshot",
                    "Binance one-second closes use assumed close+1s availability, not observed receipt times",
                    "Rolling60s Binance spot mean is a proxy; it is not the official Chainlink TWAP",
                    "No Coinbase or Chainlink reference observations in this source",
                    "Subset hash verified locally; full remote file hash recorded but not re-downloaded to verify"],
                "hours": [{k: v for k, v in x.items() if k != "markets"} for x in hours]}
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "hours"}, indent=2), flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 30))
    parser.add_argument("--hour", type=int, default=16)
    parser.add_argument("--output", type=Path, default=ROOT / "data/btc_expanded_replay.csv")
    args = parser.parse_args()
    run(args.start, args.end, args.hour, args.output)
