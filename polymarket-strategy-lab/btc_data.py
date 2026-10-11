"""Extract receive-time BTC book replay features; never submit orders.

Run with ../Polymarket_5m_btc/.venv/Scripts/python.exe btc_data.py.
Each decision at UTC second T sees events with local_receive_ts strictly < T.
The source database is read-only. Invalid reconstructed books remain missing.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = ROOT.parent / "Polymarket_5m_btc/data/runs/sqlite-2h-20260907T165500Z"
QUOTE_TYPES = {"book", "price_change", "best_bid_ask"}


def epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def number(value):
    return None if value is None or value == "" else float(value)


@dataclass
class Book:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    valid: bool = False
    connection_id: str | None = None
    quote_ts: float | None = None
    source_ts: float | None = None
    snapshot_ts: float | None = None
    tick_size: float | None = None
    reason: str = "no_snapshot"
    max_receive_gap_seconds: float | None = 5
    reconcile_after_delta: bool = False
    allow_one_sided: bool = False

    def top(self):
        bid = max(self.bids, default=None)
        ask = min(self.asks, default=None)
        return bid, ask

    def update(self, event: dict, counts: Counter) -> None:
        kind, ts = event["event_type"], event["recv_ts"]
        connection = event.get("connection_id")
        if self.connection_id is not None and connection != self.connection_id:
            self.valid, self.reason = False, "connection_change"
            counts["connection_invalidations"] += 1
        if (self.max_receive_gap_seconds is not None and self.quote_ts is not None
                and ts - self.quote_ts > self.max_receive_gap_seconds):
            self.valid, self.reason = False, "receive_gap"
            counts["receive_gap_invalidations"] += 1
        self.connection_id = connection
        if kind == "tick_size_change":
            body = json.loads(event["raw_payload"])
            body = body.get("payload", body)
            self.tick_size = number(body.get("new_tick_size"))
            return
        self.quote_ts = ts
        self.source_ts = (event["source_event_ts"] / 1e6
                          if event.get("source_event_ts") else None)
        if kind == "book":
            body = json.loads(event["raw_payload"])
            body = body.get("payload", body)
            self.bids = {float(x["price"]): float(x["size"]) for x in body["bids"]
                         if float(x["size"]) > 0}
            self.asks = {float(x["price"]): float(x["size"]) for x in body["asks"]
                         if float(x["size"]) > 0}
            self.snapshot_ts = ts
            self.valid, self.reason = True, ""
        elif kind == "price_change" and (self.valid or (
                self.reconcile_after_delta and self.snapshot_ts is not None
                and (self.reason == "reported_bbo_mismatch" or (
                    self.allow_one_sided and self.reason == "empty_or_crossed_book")))):
            recovering = not self.valid
            body = json.loads(event["raw_payload"])
            change = body.get("price_change")
            if change is None or str(change.get("asset_id") or change.get("token_id")) != event["symbol"]:
                self.valid, self.reason = False, "missing_token_delta"
                counts["missing_token_delta"] += 1
                return
            price, size = float(change["price"]), float(change["size"])
            if change["side"] not in {"BUY", "SELL"} or size < 0:
                raise ValueError("Invalid depth delta")
            levels = self.bids if change["side"] == "BUY" else self.asks
            if size == 0:
                levels.pop(price, None)
            else:
                levels[price] = size
            if recovering:
                self.valid, self.reason = True, ""
                counts["delta_reconciliation_checks"] += 1
        if not self.valid:
            return
        bid, ask = self.top()
        invalid = bid is None or ask is None or not 0 < bid < ask < 1
        if self.allow_one_sided:
            invalid = ((bid is None and ask is None)
                or (bid is not None and not 0 < bid < 1)
                or (ask is not None and not 0 < ask < 1)
                or (bid is not None and ask is not None and bid >= ask))
        # Weather's BBO uses 0/1 for an absent bid/ask. These sentinels never
        # create executable levels: only observed snapshot/delta depth does.
        reported_bid = 0 if self.allow_one_sided and bid is None else bid
        reported_ask = 1 if self.allow_one_sided and ask is None else ask
        if invalid:
            self.valid, self.reason = False, "empty_or_crossed_book"
            counts["empty_or_crossed_book"] += 1
        elif ((event.get("bid") is not None and abs(reported_bid - float(event["bid"])) > 1e-8)
              or (event.get("ask") is not None and abs(reported_ask - float(event["ask"])) > 1e-8)):
            self.valid, self.reason = False, "reported_bbo_mismatch"
            counts["reported_bbo_mismatch"] += 1

    def features(self, prefix: str, ts: int) -> dict:
        bid, ask = self.top() if self.valid else (None, None)
        return {f"{prefix}_{k}": v for k, v in {
            "bid": bid, "ask": ask,
            "bid_size": self.bids.get(bid), "ask_size": self.asks.get(ask),
            "quote_recv_ts": self.quote_ts, "quote_source_ts": self.source_ts,
            "quote_age_seconds": ts - self.quote_ts if self.quote_ts else None,
            "book_snapshot_age_seconds": ts - self.snapshot_ts if self.snapshot_ts else None,
            "book_valid": self.valid, "invalid_reason": self.reason,
            "connection_id": self.connection_id, "tick_size": self.tick_size,
            "min_order_size": None,
        }.items()}


def replay_rows(events, markets: list[dict], counts: Counter):
    """Stream ordered events into one active-market feature row per second."""
    books = {str(m[k]): Book() for m in markets for k in ("up_token_id", "down_token_id")}
    refs = {}
    next_second = None
    previous_receive = None

    def snapshot(ts):
        active = [m for m in markets if m["window_start_ts"] <= ts < m["window_end_ts"]]
        for market in active:
            row = {"decision_ts": ts, **market}
            row.update(books[market["up_token_id"]].features("up", ts))
            row.update(books[market["down_token_id"]].features("down", ts))
            for name in ("binance", "coinbase", "chainlink30", "chainlink60"):
                ref = refs.get(name, {})
                for key in ("bid", "ask", "price", "recv_ts", "source_ts"):
                    row[f"{name}_{key}"] = ref.get(key)
                row[f"{name}_age_seconds"] = ts - ref["recv_ts"] if ref else None
            counts["feature_rows"] += 1
            if row["up_book_valid"] and row["down_book_valid"]:
                counts["valid_paired_rows"] += 1
                if max(row["up_quote_age_seconds"], row["down_quote_age_seconds"]) <= 2:
                    counts["valid_paired_rows_age_le_2s"] += 1
            yield row

    for event in events:
        ts = event.get("recv_ts")
        if ts is None:
            ts = event["recv_ts"] = epoch(event["local_receive_ts"])
        if previous_receive is not None and ts < previous_receive:
            raise ValueError("Receive-time stream is out of order")
        previous_receive = ts
        if next_second is None:
            next_second = math.floor(ts) + 1
        while next_second <= ts:
            yield from snapshot(next_second)
            next_second += 1
        source, kind = event["source"], event["event_type"]
        counts[f"events/{source}/{kind}"] += 1
        if source == "polymarket" and event["symbol"] in books:
            books[event["symbol"]].update(event, counts)
        elif ((source == "binance" and kind == "book_ticker" and event["symbol"] == "BTCUSDT")
              or (source == "coinbase" and kind == "ticker" and event["symbol"] == "BTC-USD")):
            bid, ask = number(event.get("bid")), number(event.get("ask"))
            if bid is not None and ask is not None and 0 < bid <= ask:
                refs[source] = {"bid": bid, "ask": ask, "price": (bid + ask) / 2,
                    "recv_ts": ts, "source_ts": event["source_event_ts"] / 1e6
                    if event.get("source_event_ts") else None}
        elif source == "chainlink" and kind in {"chainlink_twap_30", "chainlink_twap_60"}:
            refs["chainlink" + kind.rsplit("_", 1)[1]] = {
                "price": number(event.get("price")), "recv_ts": ts,
                "source_ts": event["source_event_ts"] / 1e6
                if event.get("source_event_ts") else None}
    # Do not extrapolate beyond the last actual event received.


def extract(run: Path, output: Path) -> dict:
    database = run / "events.sqlite3"
    source_stat = database.stat()
    print(f"Hashing {source_stat.st_size:,} source bytes", flush=True)
    with database.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    master = pd.read_parquet(run / "market_master/markets.parquet")
    markets = []
    for row in master.to_dict(orient="records"):
        if not str(row["slug"]).startswith("btc-updown-5m-"):
            continue
        markets.append({"market_id": row["condition_id"], "slug": row["slug"],
            "up_token_id": row["up_token_id"], "down_token_id": row["down_token_id"],
            "window_start_ts": int(row["window_start_ts"]) // 1_000_000,
            "window_end_ts": int(row["window_end_ts"]) // 1_000_000})
    counts = Counter()
    con = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    bounds = [con.execute(f"SELECT local_receive_ts FROM events ORDER BY local_receive_ts {order} LIMIT 1").fetchone()[0]
              for order in ("ASC", "DESC")]
    query = """SELECT source,symbol,market_id,event_type,source_event_ts,local_receive_ts,
          price,bid,ask,connection_id,raw_payload
        FROM events INDEXED BY idx_events_receive
        WHERE (source='polymarket' AND event_type IN ('book','price_change','best_bid_ask','tick_size_change'))
           OR (source='binance' AND event_type='book_ticker')
           OR (source='coinbase' AND event_type='ticker')
           OR source='chainlink'
        ORDER BY local_receive_ts,event_id"""
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".part")
    print("Reconstructing books in strict local receive-time order", flush=True)
    with partial.open("w", newline="", encoding="utf-8") as handle:
        writer = None
        for row in replay_rows((dict(row) for row in con.execute(query)), markets, counts):
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)
            if counts["feature_rows"] % 600 == 0:
                print(f"rows={counts['feature_rows']} valid_pairs={counts['valid_paired_rows']}", flush=True)
    con.close()
    final_stat = database.stat()
    if (final_stat.st_size, final_stat.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
        raise RuntimeError("Source changed while extracting; partial output is not valid")
    collection = json.loads((run / "collection_manifest.json").read_text())
    manifest = {
        "status": "extracted", "created_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(database.resolve()), "source_sha256": sha,
        "source_bytes": source_stat.st_size, "source_receive_bounds": bounds,
        "source_collection_status": collection["status"],
        "source_collection_errors": collection["errors"],
        "source_gap_count": collection.get("gap_count"), "market_count": len(markets),
        "counts": dict(sorted(counts.items())), "output": str(output.resolve()),
        "semantics": "UTC second T uses only events received strictly before T; books reset on connection changes, >5s receive gaps or BBO mismatch; stale rows retained with explicit ages",
        "limitations": ["Only one two-hour collection; inadequate by itself for profitability validation",
            "No settlement outcomes in source; obtain actual Gamma outcomes independently",
            "Order queue position, hidden liquidity and fill latency are unobserved",
            "Book tick size is only populated after an observed change; minimum order size is unavailable",
            "Chainlink60 is a 60-second TWAP consistent with the current published resolution family; source gaps and exact boundary observations remain incomplete"],
    }
    partial.replace(output)
    with output.open("rb") as handle:
        manifest["output_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=ROOT / "data/btc_replay.csv")
    args = parser.parse_args()
    extract(args.run, args.output)
