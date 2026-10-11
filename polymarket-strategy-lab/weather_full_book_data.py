"""Acquire the complete fixed 30-day Chicago cohort before any L2 PnL evaluation.

Public archive receipt ordering is causal; collector continuity remains an
assumption. This extractor contains neither forecast selection nor settlement.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from expanded_book_data import cached_json, connect, sha256
from weather_book_check import archive_depth_events, sample_books, usable
from weather_data import CONFIG, DATA, ROOT, dates_between, read_json, write_json

CACHE = ROOT / "data/weather_full_books"
RESULT = ROOT / "research/weather_full_book_data"
FREEZE = ROOT / "research/weather_full_book_acquisition_config_v2.json"


def frozen_scope():
    return {
        "version": 2, "start_date": "2026-08-31", "end_date": "2026-09-29",
        "expected_days": 30, "expected_markets_per_day": 11,
        "selection": "Every Yes and No token for all 11 Chicago daily temperature conditions; no signal or outcome selection",
        "decision_utc_hour": 15, "primary_book_age_seconds": 60,
        "secondary_book_age_seconds": [5, 300],
        "execution_latency_seconds": 60, "initial_lookback_seconds": 3600,
        "end_after_decision_seconds": 120, "missing_snapshot_max_lookback_hours": 24,
        "missing_snapshot_policy": "Extend earlier hourly partitions only for tradable conditions missing an initial snapshot, up to 24 hours before decision; exclude historically closed conditions from backfill and retain missing data explicitly",
        "event_types": ["book", "best_bid_ask", "price_change", "tick_size_change"],
        "causality": "Use only timestamp_received strictly less than each decision boundary; depth state follows received sequence",
        "continuity": "Archive collector continuity assumed across quiet intervals and hourly partitions, not independently proven",
        "reconciliation": "Initial full snapshot required; actual deltas continue across transient BBO mismatch or empty/crossed state; both known top prices must match reported BBO, with 0/1 representing absent bid/ask",
        "one_sided_policy": "Known one-sided depth allowed; executable BUY still requires a real ask with positive observed size; no fabricated ask at sentinel1",
        "current_market_metadata_usage": "Condition/token identity and bucket label; historical closedTime/resolution timestamp only for ineligible-closed flag, never outcome/probability features; orderMinSize reference-only",
        "pre_pnl_revision": "v2 repairs BTC-specific two-sided and stopped-delta assumptions and excludes closed conditions from backfill; changed before any full-cohort PnL evaluation",
        "weather_strategy_config_sha256": sha256(CONFIG),
        "unchanged_strategy": {"forecast_min_net_edge": .08, "order_shares": 5,
            "base_price_markup": .01, "stress_price_markups": [.025, .05],
            "forecast_mu_sigma": "Reuse original 30-day calibration; do not refit on these days"},
        "study_status": "Exploratory follow-up after trade-print study and conditional 20-signal L2 audit; not a new untouched holdout",
    }


def freeze():
    expected = frozen_scope()
    if FREEZE.exists():
        actual = read_json(FREEZE)
        if {k: v for k, v in actual.items() if k != "frozen_at_utc"} != expected:
            raise ValueError("Frozen full-book scope differs; do not silently change evaluation scope")
    else:
        write_json(FREEZE, {**expected, "frozen_at_utc": datetime.now(timezone.utc).isoformat()})
    return expected


def markets_for_day(ds):
    metadata = DATA / "events" / f"{ds}.json"
    events = read_json(metadata)["body"]
    if len(events) != 1 or len(events[0]["markets"]) != 11:
        raise ValueError(f"Expected exactly one event and 11 markets for {ds}")
    markets, seen_conditions, seen_tokens = [], set(), set()
    for item in events[0]["markets"]:
        cid = item["conditionId"]
        outcomes, tokens = json.loads(item["outcomes"]), json.loads(item["clobTokenIds"])
        if set(outcomes) != {"Yes", "No"} or len(outcomes) != 2 or len(tokens) != 2:
            raise ValueError(f"Invalid binary mapping: {cid}")
        if cid in seen_conditions or len(set(tokens)) != 2 or seen_tokens.intersection(tokens):
            raise ValueError(f"Duplicate condition/token: {cid}")
        seen_conditions.add(cid)
        seen_tokens.update(tokens)
        close = datetime.fromisoformat(item["closedTime"].replace("Z", "+00:00")).timestamp()
        resolved = read_json(DATA / "resolutions" / f"{cid}.json")["body"]["data"]
        resolved = [r for r in resolved if r["condition_id"] == cid and r["status"] == "resolved"]
        if len(resolved) != 1:
            raise ValueError(f"Expected one actual resolution record for {cid}")
        stop = min(close, int(resolved[0]["last_update_timestamp"]))
        markets.append({"condition_id": cid, "bucket": item.get("groupItemTitle"),
            "tokens": dict(zip(outcomes, tokens)),
            "trading_stop_timestamp": stop,
            "current_min_order_shares_reference_only": item.get("orderMinSize")})
    return sorted(markets, key=lambda m: m["condition_id"]), sha256(metadata)


def subset(con, ds, hour, conditions, start, end):
    conditions = sorted(conditions)
    # A full day's normal scope has stable short names; recovery scope is hashed.
    import hashlib
    scope = hashlib.sha256(json.dumps(conditions).encode()).hexdigest()[:12]
    stem = f"{ds}T{hour:02d}-{scope}"
    parquet, meta = CACHE / f"{stem}.parquet", CACHE / f"{stem}.manifest.json"
    url = f"https://dl.pendulumflow.com/v3/{ds}/{hour:02d}/{ds}T{hour:02d}.parquet"
    manifest_url = f"https://archive.pendulumflow.com/v3/{ds}/{hour:02d}/manifest.json"
    if parquet.exists() and meta.exists():
        prior = read_json(meta)
        if (prior["condition_ids"] == conditions and prior["start"] == start
                and prior["end"] == end and prior["subset_sha256"] == sha256(parquet)):
            return parquet, prior
    source_path = CACHE / f"{ds}T{hour:02d}.source.json"
    source = cached_json(manifest_url, source_path)["body"]
    partial = parquet.with_suffix(".parquet.part")
    con.execute("""COPY (SELECT event_type,timestamp_received,timestamp,market,asset_id,
          best_bid,best_ask,bids,asks,new_tick_size,sequence,price,size,side
        FROM read_parquet($source)
        WHERE market IN (SELECT unnest($conditions))
          AND event_type IN ('book','best_bid_ask','tick_size_change','price_change')
          AND timestamp_received>=to_timestamp($start)
          AND timestamp_received<to_timestamp($end))
        TO $destination (FORMAT PARQUET)""", {
            "source": url, "conditions": [bytes.fromhex(c[2:]) for c in conditions],
            "start": start, "end": end, "destination": str(partial)})
    partial.replace(parquet)
    stats = con.execute("""SELECT event_type,count(*),min(epoch(timestamp_received)),
        max(epoch(timestamp_received)) FROM read_parquet(?) GROUP BY event_type""", [str(parquet)]).fetchall()
    record = {"condition_ids": conditions, "start": start, "end": end,
        "rows": sum(row[1] for row in stats),
        "event_counts": {row[0]: row[1] for row in stats},
        "first_recv_ts": min((row[2] for row in stats), default=None),
        "last_recv_ts": max((row[3] for row in stats), default=None),
        "source_url": url, "source_manifest_url": manifest_url,
        "source_published_sha256": source.get("sha256"),
        "source_published_bytes": source.get("bytes"),
        "source_manifest_sha256": sha256(source_path),
        "subset_sha256": sha256(parquet), "subset_bytes": parquet.stat().st_size,
        "subset_path": str(parquet.relative_to(ROOT))}
    write_json(meta, record)
    return parquet, record


def acquire_day(con, day, scope):
    ds = day.isoformat()
    markets, metadata_hash = markets_for_day(ds)
    decision = int(datetime(day.year, day.month, day.day, 15, tzinfo=timezone.utc).timestamp())
    conditions = [m["condition_id"] for m in markets]
    events, parts = [], []
    for hour, start, end in ((14, decision - 3600, decision), (15, decision, decision + 120)):
        path, record = subset(con, ds, hour, conditions, start, end)
        parts.append(record)
        events.extend(archive_depth_events(con, path, ds))
    # A narrow missing seed is not evidence of no liquidity. Extend causally.
    for lookback in range(2, scope["missing_snapshot_max_lookback_hours"] + 1):
        seeded = {e["symbol"] for e in events if e["event_type"] == "book" and e["recv_ts"] < decision}
        missing = [m["condition_id"] for m in markets
                   if m.get("trading_stop_timestamp", float("inf")) > decision
                   and not set(m["tokens"].values()).issubset(seeded)]
        if not missing:
            break
        start = decision - lookback * 3600
        at = datetime.fromtimestamp(start, timezone.utc)
        path, record = subset(con, at.date().isoformat(), at.hour, missing, start, start + 3600)
        parts.append(record)
        events.extend(archive_depth_events(con, path, ds))
    by_token = defaultdict(list)
    for event in events:
        by_token[event["symbol"]].append(event)
    rows = []
    for market in markets:
        for outcome, token in market["tokens"].items():
            snapshots, counts = sample_books(by_token[token], token, decision,
                scope["execution_latency_seconds"], allow_one_sided=True)
            rows.append({"date": ds, "condition_id": market["condition_id"],
                "token_id": token, "outcome": outcome, "bucket": market["bucket"],
                "decision_timestamp": decision, **snapshots,
                "trading_stop_timestamp": market.get("trading_stop_timestamp"),
                "ineligible_closed_at_decision": market.get("trading_stop_timestamp", float("inf")) <= decision,
                "current_min_order_shares_reference_only": market["current_min_order_shares_reference_only"],
                "minimum_order_notional": None, "reconstruction_counts": counts})
    return rows, {"date": ds, "metadata_sha256": metadata_hash,
        "market_count": len(markets), "outcome_count": len(rows),
        "rows": sum(p["rows"] for p in parts), "partitions": parts}


def main():
    scope = freeze()
    CACHE.mkdir(parents=True, exist_ok=True)
    RESULT.mkdir(parents=True, exist_ok=True)
    con, all_rows, completed, errors = connect(), [], [], []
    for day in dates_between(scope["start_date"], scope["end_date"]):
        try:
            rows, meta = acquire_day(con, day, scope)
            all_rows.extend(rows)
            completed.append(meta)
            missing = sum(r["signal"]["last_full_snapshot"] is None for r in rows)
            print(f"{day} markets={meta['market_count']} outcomes={len(rows)} archive_rows={meta['rows']} missing_seed={missing}", flush=True)
            write_json(CACHE / f"{day}.snapshots.json", rows)
        except Exception as exc:
            errors.append({"date": day.isoformat(), "error": f"{type(exc).__name__}: {exc}"})
            print(f"{day} ERROR {type(exc).__name__}: {exc}", flush=True)
        write_json(RESULT / "progress.json", {"requested_days": 30,
            "completed_days": len(completed), "outcome_count": len(all_rows), "errors": errors})
    con.close()
    output = CACHE / "snapshots.jsonl"
    partial = output.with_suffix(".jsonl.part")
    partial.write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in all_rows), encoding="utf-8")
    partial.replace(output)
    counts = {str(age): {name: sum(usable(r[name], age)[0] for r in all_rows)
                         for name in ("signal", "execution")} for age in (5, 60, 300)}
    complete = not errors and len(completed) == 30 and len(all_rows) == 660
    manifest = {"complete": complete, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope_path": str(FREEZE.relative_to(ROOT)), "scope_sha256": sha256(FREEZE),
        "extractor_sources_sha256": {name: sha256(ROOT / name) for name in (
            "weather_full_book_data.py", "weather_book_check.py", "btc_data.py", "expanded_book_data.py")},
        "original_weather_config_sha256": sha256(CONFIG),
        "requested_days": 30, "completed_days": len(completed),
        "requested_markets": 330, "market_count": sum(d["market_count"] for d in completed),
        "expected_outcome_snapshots": 660, "outcome_snapshots": len(all_rows),
        "archive_rows": sum(d["rows"] for d in completed), "errors": errors,
        "snapshots_path": str(output.relative_to(ROOT)), "snapshots_sha256": sha256(output),
        "primary_book_age_seconds": 60, "usable_by_age_seconds": counts,
        "missing_signal_initial_snapshot": sum(r["signal"]["last_full_snapshot"] is None for r in all_rows),
        "ineligible_closed_outcomes": sum(r["ineligible_closed_at_decision"] for r in all_rows),
        "no_ask_signal_outcomes": sum(r["signal"]["book_book_valid"] and r["signal"]["book_ask"] is None for r in all_rows),
        "invalid_signal_reasons": dict(Counter(r["signal"]["book_invalid_reason"] for r in all_rows if not r["signal"]["book_book_valid"])),
        "days": completed, "limitations": [
            "Exploratory follow-up using all 30 original OOS days, not a fresh holdout",
            "Acquisition completeness does not imply every quote is fresh or valid",
            "Archive collector receipt differs from a hypothetical trader's receipt",
            "Archive continuity and complete delivery of depth deltas are assumed, not independently proven",
            "Quote and size observations do not prove a hypothetical live fill",
            "Historical minimum order notional and initial tick may be unknown; current metadata is reference only",
            "Published full-archive hashes retained but full remote files not independently downloaded and hashed"]}
    write_json(RESULT / "manifest.json", manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != "days"}, indent=2), flush=True)
    if not complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
