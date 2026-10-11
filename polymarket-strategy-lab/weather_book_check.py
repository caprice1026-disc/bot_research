"""Supplemental L2 audit of the frozen weather strategy's existing OOS attempts.

This conditions on already selected trade-tape signals: it is not a full L2
strategy backtest and does not search for alternative candidates or fill times.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from btc_data import Book
from expanded_book_data import cached_json, connect, sha256
from weather_data import CONFIG, DATA, ROOT, read_json, write_json

CACHE = ROOT / "data/weather_books"
RESULT = ROOT / "research/weather_book_check"
FRESHNESS_SECONDS = 5


def subset(con, ds: str, hour: int, cid: str, start: int, end: int):
    stem = f"{ds}T{hour:02d}-{cid[2:14]}-full-l2"
    parquet, meta = CACHE / f"{stem}.parquet", CACHE / f"{stem}.manifest.json"
    url = f"https://dl.pendulumflow.com/v3/{ds}/{hour:02d}/{ds}T{hour:02d}.parquet"
    manifest_url = f"https://archive.pendulumflow.com/v3/{ds}/{hour:02d}/manifest.json"
    if parquet.exists() and meta.exists():
        prior = read_json(meta)
        if (prior["condition_id"] == cid and prior["start"] == start and prior["end"] == end
                and prior["subset_sha256"] == sha256(parquet)):
            return parquet, prior
    source_meta_file = CACHE / f"{ds}T{hour:02d}.source.json"
    source = cached_json(manifest_url, source_meta_file)["body"]
    partial = parquet.with_suffix(".parquet.part")
    con.execute("""COPY (SELECT event_type,timestamp_received,timestamp,market,asset_id,
          best_bid,best_ask,bids,asks,new_tick_size,sequence,price,size,side
        FROM read_parquet($source)
        WHERE market=$condition
          AND event_type IN ('book','best_bid_ask','tick_size_change','price_change')
          AND timestamp_received>=to_timestamp($start)
          AND timestamp_received<to_timestamp($end))
        TO $destination (FORMAT PARQUET)""", {
            "source": url, "condition": bytes.fromhex(cid[2:]), "start": start,
            "end": end, "destination": str(partial)})
    partial.replace(parquet)
    count = con.execute("SELECT count(*) FROM read_parquet(?)", [str(parquet)]).fetchone()[0]
    record = {"condition_id": cid, "start": start, "end": end, "rows": count,
        "source_url": url, "source_manifest_url": manifest_url,
        "source_published_sha256": source.get("sha256"),
        "source_published_bytes": source.get("bytes"),
        "source_manifest_sha256": sha256(source_meta_file),
        "subset_sha256": sha256(parquet), "subset_bytes": parquet.stat().st_size}
    write_json(meta, record)
    return parquet, record


def archive_depth_events(con, path: Path, ds: str):
    rows = con.execute("""SELECT event_type,epoch(timestamp_received),epoch_us(timestamp),
          asset_id,best_bid,best_ask,bids,asks,new_tick_size,price,size,side
        FROM read_parquet(?) ORDER BY timestamp_received,sequence,event_type""", [str(path)]).fetchall()
    for kind, received, source, raw_token, bid, ask, bids, asks, tick, price, size, side in rows:
        token = str(int.from_bytes(raw_token, "big"))
        if kind == "book":
            payload = {"bids": bids, "asks": asks}
        elif kind == "price_change":
            payload = {"price_change": {"asset_id": token, "price": price, "size": size, "side": side}}
        else:
            payload = {"new_tick_size": tick}
        yield {"source": "polymarket", "symbol": token, "event_type": kind,
            "recv_ts": received, "source_event_ts": source,
            "connection_id": "archive-continuity-assumption:" + ds,
            "bid": float(bid) if bid is not None else None,
            "ask": float(ask) if ask is not None else None,
            "raw_payload": json.dumps(payload, default=float)}


def sample_books(events, token: str, decision: int, delay: int, *, allow_one_sided=False):
    """Snapshots exclude events received exactly at either decision boundary."""
    # Quiet weather books are not proof of a transport outage. Archive continuity
    # is an explicit assumption; quote-age sensitivities are reported separately.
    book, counts, snapshots = Book(max_receive_gap_seconds=None, reconcile_after_delta=True,
        allow_one_sided=allow_one_sided), Counter(), {}
    index = 0
    reported = {}
    full_snapshot = {}
    events = sorted((e for e in events if e["symbol"] == token), key=lambda e: e["recv_ts"])
    for name, at in (("signal", decision), ("execution", decision + delay)):
        while index < len(events) and events[index]["recv_ts"] < at:
            event = events[index]
            book.update(event, counts)
            if event["event_type"] == "book":
                bid, ask = book.top()
                full_snapshot = {"bid": bid, "ask": ask, "bid_size": book.bids.get(bid),
                    "ask_size": book.asks.get(ask), "recv_ts": event["recv_ts"],
                    "source_ts": event["source_event_ts"] / 1e6 if event.get("source_event_ts") else None}
                reported = {k: full_snapshot[k] for k in ("bid", "ask", "recv_ts", "source_ts")}
            elif event["event_type"] in {"best_bid_ask", "price_change"}:
                reported = {"bid": event["bid"], "ask": event["ask"], "recv_ts": event["recv_ts"],
                    "source_ts": event["source_event_ts"] / 1e6 if event.get("source_event_ts") else None}
            index += 1
        snapshots[name] = {"timestamp": at, **book.features("book", at),
            "last_reported_quote": {**reported, "age_seconds": at - reported["recv_ts"]} if reported else None,
            "last_full_snapshot": {**full_snapshot, "age_seconds": at - full_snapshot["recv_ts"]} if full_snapshot else None}
    return snapshots, dict(counts)


def usable(snapshot, freshness=FRESHNESS_SECONDS):
    if not snapshot["book_book_valid"]:
        return False, snapshot["book_invalid_reason"]
    if snapshot["book_ask"] is None or not snapshot["book_ask_size"] or snapshot["book_ask_size"] <= 0:
        return False, "no_ask_depth"
    for key in ("book_quote_age_seconds",):
        if snapshot[key] is None or not 0 <= snapshot[key] <= freshness:
            return False, "stale_" + key
    source = snapshot["book_quote_source_ts"]
    if source is None or not 0 <= snapshot["timestamp"] - source <= freshness:
        return False, "stale_source_timestamp"
    return True, "usable"


def audit_one(con, attempt: dict, cfg: dict):
    ds, cid, decision = attempt["date"], attempt["condition_id"], int(attempt["decision_timestamp"])
    metadata = read_json(DATA / "events" / f"{ds}.json")["body"][0]
    markets = [m for m in metadata["markets"] if m["conditionId"] == cid]
    if len(markets) != 1:
        raise ValueError("Condition must uniquely match original event metadata")
    market = markets[0]
    outcomes, tokens = json.loads(market["outcomes"]), json.loads(market["clobTokenIds"])
    token = tokens[outcomes.index(attempt["outcome"])]
    parts, events = [], []
    for hour, start, end in ((14, decision - 3600, decision), (15, decision, decision + 120)):
        parquet, manifest = subset(con, ds, hour, cid, start, end)
        parts.append(manifest)
        events.extend(archive_depth_events(con, parquet, ds))
    snapshots, counts = sample_books(events, token, decision, cfg["execution_latency_seconds"])
    signal, execution = snapshots["signal"], snapshots["execution"]
    signal_ok, signal_reason = usable(signal)
    execution_ok, execution_reason = usable(execution)
    signal_edge, price, cost, shares = None, None, None, cfg["order_shares"]
    edge_ok, limit_ok, size_ok, before_close = False, False, False, False
    close = datetime.fromisoformat(market["closedTime"].replace("Z", "+00:00")).timestamp()
    resolution = read_json(DATA / "resolutions" / f"{cid}.json")["body"]["data"]
    stop = min(close, int(next(r for r in resolution if r["condition_id"] == cid and r["status"] == "resolved")["last_update_timestamp"]))
    if signal_ok:
        price_at_signal = signal["book_ask"] + cfg["base_price_markup"]
        signal_edge = attempt["model_probability"] - price_at_signal - cfg["current_taker_fee_rate"] * price_at_signal * (1 - price_at_signal)
        edge_ok = 0 < price_at_signal < 1 and signal_edge >= cfg["forecast_min_net_edge"]
    if execution_ok:
        price = execution["book_ask"] + cfg["base_price_markup"]
        limit_ok = 0 < price < 1 and price <= attempt["signal_price"] + cfg["max_price_move"]
        size_ok = execution["book_ask_size"] >= shares
        before_close = decision + cfg["execution_latency_seconds"] < stop
        if 0 < price < 1:
            fee = Decimal(str(shares)) * Decimal(str(cfg["current_taker_fee_rate"])) * Decimal(str(price)) * (1 - Decimal(str(price)))
            cost = shares * price + float(fee.quantize(Decimal(".00001"), rounding=ROUND_HALF_UP))
    sensitivity = {}
    for age in (5, 60, 300):
        s_ok, s_reason = usable(signal, age)
        e_ok, e_reason = usable(execution, age)
        s_price = signal["book_ask"] + cfg["base_price_markup"] if s_ok else None
        e_price = execution["book_ask"] + cfg["base_price_markup"] if e_ok else None
        good_edge = (s_price is not None and 0 < s_price < 1 and attempt["model_probability"] - s_price
            - cfg["current_taker_fee_rate"] * s_price * (1 - s_price) >= cfg["forecast_min_net_edge"])
        good_fill = (e_price is not None and 0 < e_price < 1
            and e_price <= attempt["signal_price"] + cfg["max_price_move"]
            and execution["book_ask_size"] >= shares
            and decision + cfg["execution_latency_seconds"] < stop)
        sensitivity[str(age)] = {"signal_usable": s_ok, "execution_usable": e_ok,
            "signal_reason": s_reason, "execution_reason": e_reason,
            "edge_survives_actual_ask": good_edge, "all_conditional_gates_pass": bool(good_edge and good_fill)}
    return {"date": ds, "condition_id": cid, "token_id": token, "outcome": attempt["outcome"],
        "decision_timestamp": decision, "original_attempt_reason": attempt["reason"],
        "original_signal_price": attempt["signal_price"], "model_probability": attempt["model_probability"],
        "signal": signal, "execution": execution, "signal_usable": signal_ok,
        "signal_book_reason": signal_reason, "execution_usable": execution_ok,
        "execution_book_reason": execution_reason, "actual_ask_based_net_edge": signal_edge,
        "edge_survives_actual_ask": edge_ok, "hypothetical_execution_price": price,
        "fixed_original_price_limit_pass": limit_ok, "top_depth_sufficient": size_ok,
        "before_trading_stop": before_close, "estimated_cost_with_current_fee": cost,
        "all_conditional_gates_pass": signal_ok and execution_ok and edge_ok and limit_ok and size_ok and before_close,
        "metadata_min_order_shares_current_reference_only": market.get("orderMinSize"),
        "minimum_order_notional": None, "historical_tick_size": execution["book_tick_size"],
        "age_sensitivities": sensitivity, "reconstruction_counts": counts, "subsets": parts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    cfg = read_json(CONFIG)
    attempt_path = ROOT / "research/weather_results/attempts.json"
    attempts = [a for a in read_json(attempt_path) if a["strategy"] == "forecast_probability_edge" and a["period"] == "out_of_sample"]
    if args.limit:
        attempts = attempts[:args.limit]
    con, rows, errors = connect(), [], []
    for attempt in attempts:
        try:
            result = audit_one(con, attempt, cfg)
            rows.append(result)
            print(f"{attempt['date']} signal={result['signal_book_reason']} execution={result['execution_book_reason']} pass={result['all_conditional_gates_pass']}", flush=True)
        except Exception as exc:
            errors.append({"date": attempt["date"], "condition_id": attempt["condition_id"], "error": f"{type(exc).__name__}: {exc}"})
            print(f"{attempt['date']} ERROR {type(exc).__name__}: {exc}", flush=True)
        write_json(RESULT / "progress.json", {"completed_attempts": len(rows), "errors": errors, "requested_attempts": len(attempts)})
    con.close()
    report = {"status": "partial" if errors else "audited", "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "selected_attempts_sha256": sha256(attempt_path), "selected_attempt_count": len(attempts),
        "audited_attempt_count": len(rows), "errors": errors,
        "usable_signal_books": sum(r["signal_usable"] for r in rows),
        "usable_delayed_execution_books": sum(r["execution_usable"] for r in rows),
        "all_conditional_gates_pass": sum(r["all_conditional_gates_pass"] for r in rows),
        "freshness_gate_seconds": FRESHNESS_SECONDS, "execution_delay_seconds": cfg["execution_latency_seconds"],
        "age_sensitivity_counts": {str(age): {
            "usable_signal_books": sum(r["age_sensitivities"][str(age)]["signal_usable"] for r in rows),
            "usable_execution_books": sum(r["age_sensitivities"][str(age)]["execution_usable"] for r in rows),
            "all_conditional_gates_pass": sum(r["age_sensitivities"][str(age)]["all_conditional_gates_pass"] for r in rows),
        } for age in (5, 60, 300)},
        "selection": "Only existing out-of-sample forecast trade-tape attempts, fixed before this supplemental L2 audit",
        "limitations": ["Conditional subset audit, not a full L2 strategy backtest or performance estimate",
            "Archive collector receipt differs from a hypothetical bot's network receipt",
            "Full snapshots and price changes reconstructed; BBO mismatch invalidates sizes until a subsequent depth delta reconciles both top prices or a fresh snapshot arrives",
            "Archive continuity is assumed; quiet periods are not treated as proven feed disconnections",
            "Only one preceding hour and two following minutes per selected market requested; no snapshot is a data gap, not no liquidity",
            "Published full-archive hash retained but full remote file is not downloaded or independently hashed",
            "Minimum notional and initial historical tick are not observable in these selected archive fields",
            "Quote/depth plus current-fee adverse buffer still does not prove real live execution"],
        "rows": rows}
    write_json(RESULT / "report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
