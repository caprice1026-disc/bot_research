"""One fixed daily NO-book signal diagnostic; no execution or PnL computation."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_backtest import dead_bucket, released_max
from weather_full_book_backtest import BOOK_CONFIG, BOOK_MANIFEST, SNAPSHOTS, book_reason, cost_for, load_inputs, sha256
from weather_data import CONFIG, ROOT, read_json, write_json

FREEZE = ROOT / "research/weather_dead_bucket_book_diagnostic_config.json"


def main():
    cfg, baseline = read_json(FREEZE), read_json(CONFIG)
    if sha256(CONFIG) != cfg["baseline_config_sha256"]:
        raise ValueError("Original S5 configuration changed")
    for key in ("observation_release_lag_minutes", "dead_bucket_margin_fahrenheit", "dead_bucket_no_probability",
                "dead_bucket_min_net_edge", "order_shares", "current_taker_fee_rate"):
        if cfg[key] != baseline[key]:
            raise ValueError("Diagnostic modified original S5 rule: " + key)
    if cfg["price_markup"] != baseline["base_price_markup"]:
        raise ValueError("Diagnostic changed adverse price buffer")
    days, snapshots, _, _ = load_inputs(read_json(BOOK_CONFIG))
    counts = Counter({key: 0 for key in ("decision_days", "days_with_released_observation", "no_market_checks",
        "not_dead_after_margin", "dead_by_released_station_max", "dead_but_closed", "dead_and_open",
        "book_unavailable_or_invalid", "book_has_no_ask", "empty_ask_state_fresh", "empty_ask_state_stale",
        "book_stale_or_future", "book_shallow", "valid_fresh_sufficient_book",
        "no_edge_after_markup_and_fee", "eligible_signal")})
    rejection_reasons, details, eligible = Counter(), [], []
    for day in days:
        decision = int(datetime.fromisoformat(day["date"]).replace(hour=cfg["decision_utc_hour"], tzinfo=timezone.utc).timestamp())
        high = released_max(day["observations"], decision, cfg["observation_release_lag_minutes"])
        counts["decision_days"] += 1
        counts["days_with_released_observation"] += high is not None
        for market in day["markets"]:
            counts["no_market_checks"] += 1
            if not dead_bucket(market["bounds"], high, cfg["dead_bucket_margin_fahrenheit"]):
                counts["not_dead_after_margin"] += 1
                continue
            counts["dead_by_released_station_max"] += 1
            row = snapshots[(day["date"], market["condition_id"], "No")]
            book = row["signal"]
            detail = {"date": day["date"], "condition_id": market["condition_id"], "token_id": row["token_id"],
                "bucket": market["title"], "decision_timestamp": decision, "released_station_max_f": high,
                "no_ask": book.get("book_ask"), "no_ask_size": book.get("book_ask_size"),
                "book_valid": book.get("book_book_valid"), "initial_snapshot_present": book.get("last_full_snapshot") is not None,
                "quote_recv_ts": book.get("book_quote_recv_ts"), "quote_source_ts": book.get("book_quote_source_ts")}
            if decision >= min(market["close_timestamp"], market["resolution_timestamp"]):
                counts["dead_but_closed"] += 1
                detail["reason"] = "market_closed"
            else:
                counts["dead_and_open"] += 1
                reason = book_reason(book, decision, cfg["max_quote_age_seconds"], cfg["order_shares"])
                if reason:
                    rejection_reasons[reason] += 1
                    if book.get("book_book_valid") and book.get("last_full_snapshot") is not None and book.get("book_ask") is None:
                        stage = "book_has_no_ask"
                        received, source = book.get("book_quote_recv_ts"), book.get("book_quote_source_ts")
                        fresh = received is not None and source is not None and 0 < decision - received <= cfg["max_quote_age_seconds"] and 0 <= decision - source <= cfg["max_quote_age_seconds"]
                        counts["empty_ask_state_fresh" if fresh else "empty_ask_state_stale"] += 1
                    else:
                        stage = "book_shallow" if reason == "insufficient_top_depth" else "book_stale_or_future" if "stale" in reason or "before_decision" in reason else "book_unavailable_or_invalid"
                    counts[stage] += 1
                    detail["reason"] = reason
                else:
                    counts["valid_fresh_sufficient_book"] += 1
                    price = round(book["book_ask"] + cfg["price_markup"], 10)
                    edge = cfg["dead_bucket_no_probability"] - cost_for(price, cfg)[0] / cfg["order_shares"] if price < 1 else None
                    detail.update({"modeled_entry_price": price, "model_no_probability": cfg["dead_bucket_no_probability"], "net_edge": edge})
                    if edge is not None and edge >= cfg["dead_bucket_min_net_edge"]:
                        counts["eligible_signal"] += 1
                        detail["reason"] = "eligible_signal"
                        eligible.append(detail)
                    else:
                        counts["no_edge_after_markup_and_fee"] += 1
                        detail["reason"] = "no_edge_after_markup_and_fee"
            details.append(detail)
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "config": cfg,
        "config_sha256": sha256(FREEZE), "book_manifest_sha256": sha256(BOOK_MANIFEST), "snapshots_sha256": sha256(SNAPSHOTS),
        "scope": cfg["scope"], "counts": dict(counts), "book_rejection_reasons": dict(rejection_reasons),
        "eligible_signals": eligible, "dead_bucket_checks": details, "pnl_evaluated": False,
        "limitations": ["One 10:00 Chicago decision per day only; not evidence for all 13 hourly S5 decision times.",
            "IEM observation vintage and archive transport continuity remain assumptions; no live fill is established.",
            "No price/edge thresholds or observation margin were adjusted in this diagnosis."]}
    write_json(ROOT / "research/weather_dead_bucket_book_diagnostic.json", report)
    print(json.dumps({"counts": dict(counts), "eligible_signals": eligible}, indent=2))


if __name__ == "__main__":
    main()
