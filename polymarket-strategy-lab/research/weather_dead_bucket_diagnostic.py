"""Read-only reason counts for frozen S5; no new trading rules or thresholds."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_backtest import dead_bucket, fee_per_share, last_price, load_days, load_observations, released_max, utc_decision
from weather_data import CONFIG, ROOT, read_json, write_json


def main():
    cfg = read_json(CONFIG)
    days, _ = load_days(cfg, load_observations(cfg))
    totals, unique, by_day, examples = defaultdict(Counter), defaultdict(lambda: defaultdict(set)), [], []
    for day in days:
        period = "calibration_in_sample" if day["date"] <= cfg["calibration_end"] else "out_of_sample"
        counts = Counter({stage: 0 for stage in (
            "decision_hours", "decision_hours_with_released_observation", "market_hour_checks", "not_dead_after_margin",
            "dead_by_released_station_max", "dead_but_market_already_closed", "dead_and_market_open",
            "open_dead_without_recent_no_print", "open_dead_with_recent_no_print",
            "recent_no_price_at_or_above_one_after_markup", "recent_no_insufficient_net_edge", "qualifying_signal")})
        for hour in cfg["observed_decision_local_hours"]:
            decision = utc_decision(day["date"], hour, cfg)
            high = released_max(day["observations"], decision, cfg["observation_release_lag_minutes"])
            counts["decision_hours"] += 1
            counts["decision_hours_with_released_observation"] += high is not None
            for market in day["markets"]:
                counts["market_hour_checks"] += 1
                if not dead_bucket(market["bounds"], high, cfg["dead_bucket_margin_fahrenheit"]):
                    counts["not_dead_after_margin"] += 1
                    continue
                stages = ["dead_by_released_station_max"]
                detail = {"date": day["date"], "local_hour": hour, "bucket": market["title"], "released_max_f": high}
                if decision >= min(market["resolution_timestamp"], market["close_timestamp"]):
                    stages.append("dead_but_market_already_closed")
                else:
                    stages.append("dead_and_market_open")
                    signal = last_price(market["trades"]["No"], decision, cfg["max_signal_price_age_seconds"])
                    if signal is None:
                        stages.append("open_dead_without_recent_no_print")
                    else:
                        stages.append("open_dead_with_recent_no_print")
                        price = signal["price"] + cfg["base_price_markup"]
                        detail.update({"no_print_price": signal["price"], "modeled_entry_price": price})
                        if price >= 1:
                            stages.append("recent_no_price_at_or_above_one_after_markup")
                        else:
                            edge = cfg["dead_bucket_no_probability"] - price - fee_per_share(price, cfg["current_taker_fee_rate"])
                            detail["net_edge"] = edge
                            stages.append("qualifying_signal" if edge >= cfg["dead_bucket_min_net_edge"] else "recent_no_insufficient_net_edge")
                for stage in stages:
                    counts[stage] += 1
                    unique[period][stage].add((day["date"], market["condition_id"]))
                detail["terminal_reason"] = stages[-1]
                if len([e for e in examples if e["terminal_reason"] == detail["terminal_reason"]]) < 3:
                    examples.append(detail)
        by_day.append({"date": day["date"], "period": period, "counts": dict(counts)})
        totals[period].update(counts)
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "strategy": "observed_dead_bucket_no", "thresholds_unchanged": True,
        "measurement": "Stage counts are market-hour checks, not independent trades; unique condition-days reported separately.",
        "decision_hours": cfg["observed_decision_local_hours"], "observation_release_lag_minutes": cfg["observation_release_lag_minutes"],
        "dead_margin_fahrenheit": cfg["dead_bucket_margin_fahrenheit"], "minimum_net_edge": cfg["dead_bucket_min_net_edge"],
        "assumed_no_probability": cfg["dead_bucket_no_probability"], "price_markup": cfg["base_price_markup"],
        "periods": {period: {"stage_counts": dict(counts), "unique_condition_days_by_stage": {stage: len(values) for stage, values in unique[period].items()}} for period, counts in totals.items()},
        "days": by_day, "examples": examples,
        "limitations": ["Missing recent No trade print does not prove missing executable book liquidity.",
            "This is a read-only diagnosis of the original trade-proxy rule; it does not expand S5 to full historical books.",
            "Temperature is a lagged KORD archive observation; exact official vendor publication vintage remains unverified."]}
    write_json(ROOT / "research/weather_dead_bucket_diagnostic.json", report)
    print(json.dumps(report["periods"], indent=2))


if __name__ == "__main__":
    main()
