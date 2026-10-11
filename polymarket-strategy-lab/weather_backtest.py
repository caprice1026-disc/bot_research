"""Point-in-time weather signals with explicitly assumed trade-tape execution.

Original implementation informed by CloddsBot weather probability-price edges,
PolyWeather integer-degree normal buckets and past-only error calibration, and
polybot capital/order caps. See references/SOURCE_AUDIT.md for pinned sources.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
import math
from pathlib import Path
import random
import re
import statistics

from weather_data import CONFIG, DATA, ROOT, dates_between, read_json, settlement_source, write_json


def parse_bucket(title):
    """Return inclusive integer F bounds; reject ambiguous settlement labels."""
    text = title.replace("°F", "").strip()
    if match := re.fullmatch(r"(-?\d+) or below", text):
        return -math.inf, int(match[1])
    if match := re.fullmatch(r"(-?\d+) or higher", text):
        return int(match[1]), math.inf
    if match := re.fullmatch(r"(-?\d+)-(-?\d+)", text):
        lo, hi = int(match[1]), int(match[2])
        if lo <= hi:
            return lo, hi
    raise ValueError(f"Unsupported bucket: {title}")


def bucket_probability(mu, sigma, bounds):
    if not sigma > 0:
        raise ValueError("sigma must be positive")
    lo, hi = bounds
    cdf = lambda value: (1 + math.erf((value - mu) / (sigma * math.sqrt(2)))) / 2
    return max(0.0, min(1.0, cdf(hi + 0.5) - cdf(lo - 0.5)))


def fee_per_share(price, rate):
    if not 0 < price < 1 or rate < 0:
        raise ValueError("Invalid binary fee input")
    return rate * price * (1 - price)


def last_price(trades, timestamp, max_age):
    index = bisect_left([t["timestamp"] for t in trades], timestamp) - 1
    if index < 0:
        return None
    row = trades[index]
    return row if timestamp - row["timestamp"] <= max_age else None


def execution_proxy(trades, decision, signal_price, cfg, markup):
    """First subsequent BUY only. No same-tick fill or favourable print search."""
    eligible = [t for t in trades if t["side"] == "BUY"]
    index = bisect_left([t["timestamp"] for t in eligible], decision + cfg["execution_latency_seconds"])
    if index >= len(eligible):
        return None, "no_next_buy_print"
    row = eligible[index]
    if row["timestamp"] > decision + cfg["execution_expiry_seconds"]:
        return None, "execution_timeout"
    price = math.ceil((row["price"] + markup) * 1000 - 1e-10) / 1000
    if price >= 1 or price > signal_price + cfg["max_price_move"]:
        return None, "price_limit"
    shares = cfg["order_shares"]
    if row["size"] * cfg["tape_participation_cap"] + 1e-9 < shares:
        return None, "insufficient_print_size"
    exact_price = Decimal(str(price))
    fee = float((Decimal(str(shares)) * Decimal(str(cfg["current_taker_fee_rate"])) * exact_price * (1 - exact_price)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))
    return {"fill_timestamp": row["timestamp"], "source_print_price": row["price"],
            "source_print_size": row["size"], "entry_price": price, "shares": shares,
            "fees": fee, "cost": shares * price + fee, "fill_transaction_hash": row["transaction_hash"]}, None


def load_observations(cfg):
    local = timezone(timedelta(hours=cfg["utc_offset_hours"]))
    days = defaultdict(list)
    for row in csv.DictReader(io.StringIO(read_json(DATA / "observations_hourly.json")["body"])):
        if row["station"] != "ORD" or not row["metar"].startswith("KORD ") or row["tmpf"] in ("M", ""):
            continue
        valid = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        value = float(row["tmpf"])
        if not math.isfinite(value) or value < -50 or value > 130:
            raise ValueError("Invalid station temperature")
        days[valid.astimezone(local).date().isoformat()].append({"timestamp": valid.timestamp(), "fahrenheit": value})
    return {ds: sorted(rows, key=lambda r: r["timestamp"]) for ds, rows in days.items()}


def released_max(observations, decision, lag_minutes):
    values = [o["fahrenheit"] for o in observations if o["timestamp"] + lag_minutes * 60 <= decision]
    return max(values) if values else None


def dead_bucket(bounds, max_so_far, margin):
    return max_so_far is not None and math.isfinite(bounds[1]) and max_so_far >= bounds[1] + margin


def load_days(cfg, observations):
    manifest = read_json(DATA / "manifest.json")
    if not manifest["complete"]:
        raise ValueError("Data manifest is incomplete; finish collection before evaluating")
    if manifest["config_sha256"] != hashlib.sha256(CONFIG.read_bytes()).hexdigest():
        raise ValueError("Config changed after data collection")
    days = []
    integrity = {"raw_trade_rows": 0, "duplicate_trade_rows": 0, "observation_settlement_mismatches": [],
                 "incomplete_observation_days": [], "model_grid_coordinates": {}, "fee_rates_in_gamma": [], "settlement_sources": Counter()}
    for day in dates_between(cfg["start_date"], cfg["end_date"]):
        ds = day.isoformat()
        event = read_json(DATA / "events" / f"{ds}.json")["body"][0]
        forecast = read_json(DATA / "forecasts" / f"{ds}.json")["body"]
        model_maxes = {}
        for model in cfg["forecast_models"]:
            hourly = [(t, v) for t, v in zip(forecast["hourly"]["time"], forecast["hourly"][f"temperature_2m_{model}"]) if t.startswith(ds)]
            if len(hourly) != 24 or any(v is None for _, v in hourly):
                raise ValueError(f"Incomplete forecast for {ds}/{model}")
            model_maxes[model] = max(v for _, v in hourly)
        integrity["model_grid_coordinates"][ds] = [forecast["latitude"], forecast["longitude"]]
        obs = observations.get(ds, [])
        covered_hours = {datetime.fromtimestamp(o["timestamp"], timezone(timedelta(hours=cfg["utc_offset_hours"]))).hour for o in obs}
        if len(covered_hours) < 24:
            integrity["incomplete_observation_days"].append({"date": ds, "hours": len(covered_hours)})
        if not obs:
            raise ValueError(f"No observations: {ds}")
        actual = math.floor(max(o["fahrenheit"] for o in obs) + 0.5)
        markets = []
        for market in event["markets"]:
            cid = market["conditionId"]
            source = settlement_source(market)
            integrity["settlement_sources"][source] += 1
            if json.loads(market["outcomes"]) != ["Yes", "No"]:
                raise ValueError("Station or outcome ordering changed")
            resolution = read_json(DATA / "resolutions" / f"{cid}.json")["body"]["data"]
            resolved = [r for r in resolution if r.get("condition_id") == cid and r.get("status") == "resolved"]
            if len(resolved) != 1 or not resolved[0].get("transaction_hash"):
                raise ValueError("Missing chain resolution")
            yes = int(resolved[0]["price"]) / 10**18
            if yes not in (0.0, 0.5, 1.0) or json.loads(market["outcomePrices"])[0] != str(int(yes)):
                raise ValueError("Ambiguous or mismatched settlement")
            bounds = parse_bucket(market["groupItemTitle"])
            closed = datetime.fromisoformat(market["closedTime"].replace("Z", "+00:00"))
            if closed.tzinfo is None:
                raise ValueError("Market closedTime must include a UTC offset")
            seen = set()
            by_side = {"Yes": [], "No": []}
            for file in sorted((DATA / "trades" / cid).glob("*.json")):
                for row in read_json(file)["body"]["data"]:
                    integrity["raw_trade_rows"] += 1
                    key = tuple(row.get(k) for k in ("transaction_hash", "token_id", "side", "size", "price", "timestamp"))
                    if key in seen:
                        integrity["duplicate_trade_rows"] += 1
                        continue
                    seen.add(key)
                    if row["outcome"] not in by_side or not 0 < row["price"] < 1 or row["size"] <= 0:
                        continue
                    by_side[row["outcome"]].append(row)
            for rows in by_side.values():
                rows.sort(key=lambda r: r["timestamp"])
            integrity["fee_rates_in_gamma"].append(market.get("feeSchedule", {}).get("rate"))
            markets.append({"condition_id": cid, "title": market["groupItemTitle"], "bounds": bounds,
                            "yes_settlement": yes, "resolution_timestamp": int(resolved[0]["last_update_timestamp"]),
                            "close_timestamp": closed.timestamp(), "settlement_source": source,
                            "resolution_transaction_hash": resolved[0]["transaction_hash"], "trades": by_side})
        if sum(m["yes_settlement"] for m in markets) != 1:
            raise ValueError(f"Buckets are not mutually exclusive/exhaustive: {ds}")
        winner = next(m for m in markets if m["yes_settlement"] == 1)
        if not winner["bounds"][0] <= actual <= winner["bounds"][1]:
            integrity["observation_settlement_mismatches"].append({"date": ds, "iem_high_f": actual, "winning_bucket": winner["title"]})
        days.append({"date": ds, "model_maxes": model_maxes, "actual_iem_high": actual,
                     "observations": obs, "markets": markets})
    integrity["fee_rates_in_gamma"] = sorted(set(integrity["fee_rates_in_gamma"]))
    return days, integrity


def calibrate(days, cfg):
    rows = [d for d in days if d["date"] <= cfg["calibration_end"]]
    if len(rows) < 20:
        raise ValueError("At least 20 calibration days required")
    maes = {model: statistics.mean(abs(d["actual_iem_high"] - d["model_maxes"][model]) for d in rows) for model in cfg["forecast_models"]}
    inverse = {m: 1 / max(1, e) for m, e in maes.items()}
    weights = {m: v / sum(inverse.values()) for m, v in inverse.items()}
    residuals = [d["actual_iem_high"] - sum(weights[m] * d["model_maxes"][m] for m in weights) for d in rows]
    return {"days": len(rows), "through": cfg["calibration_end"], "model_mae_f": maes, "weights": weights,
            "bias_f": statistics.mean(residuals), "sigma_f": max(cfg["sigma_floor_fahrenheit"], statistics.stdev(residuals)),
            "fit_period_is_in_sample": True}


def utc_decision(ds, hour, cfg):
    local = timezone(timedelta(hours=cfg["utc_offset_hours"]))
    return datetime.fromisoformat(ds).replace(hour=hour, tzinfo=local).timestamp()


def run_strategy(days, cfg, fit, strategy, markup):
    worst_case_total_cost = len(days) * cfg["order_shares"] * (1 + cfg["current_taker_fee_rate"] / 4)
    if worst_case_total_cost > cfg["initial_capital"]:
        raise ValueError("Study capital must cover every order even without intermediate settlement")
    fills, attempts, daily = [], [], []
    for day in days:
        ds = day["date"]
        period = "calibration_in_sample" if ds <= cfg["calibration_end"] else "out_of_sample"
        hours = [cfg["forecast_decision_local_hour"]] if strategy == "forecast_probability_edge" else cfg["observed_decision_local_hours"]
        day_pnl = 0.0
        attempted = False
        for hour in hours:
            decision = utc_decision(ds, hour, cfg)
            max_seen = released_max(day["observations"], decision, cfg["observation_release_lag_minutes"])
            mean = sum(fit["weights"][m] * day["model_maxes"][m] for m in fit["weights"]) + fit["bias_f"]
            if strategy == "forecast_probability_edge":
                run_time = datetime.fromisoformat(ds).replace(tzinfo=timezone.utc).timestamp()
                if run_time + cfg["forecast_release_lag_hours"] * 3600 > decision:
                    raise ValueError("Forecast was not available at decision")
            candidates = []
            for market in day["markets"]:
                if decision >= min(market["resolution_timestamp"], market["close_timestamp"]):
                    continue
                if strategy == "observed_dead_bucket_no":
                    if not dead_bucket(market["bounds"], max_seen, cfg["dead_bucket_margin_fahrenheit"]):
                        continue
                    probabilities = {"No": cfg["dead_bucket_no_probability"]}
                    threshold = cfg["dead_bucket_min_net_edge"]
                else:
                    prob = bucket_probability(mean, fit["sigma_f"], market["bounds"])
                    probabilities = {"Yes": prob, "No": 1 - prob}
                    threshold = cfg["forecast_min_net_edge"]
                for outcome, probability in probabilities.items():
                    signal = last_price(market["trades"][outcome], decision, cfg["max_signal_price_age_seconds"])
                    if signal is None:
                        continue
                    expected_price = signal["price"] + markup
                    if expected_price >= 1:
                        continue
                    net_edge = probability - expected_price - fee_per_share(expected_price, cfg["current_taker_fee_rate"])
                    if net_edge >= threshold:
                        candidates.append((net_edge, market["condition_id"], outcome, probability, signal, market))
            if not candidates:
                continue
            # One order attempt per event/day; ranking uses information known now.
            edge, cid, outcome, probability, signal, market = max(candidates, key=lambda item: (item[0], item[1], item[2]))
            attempted = True
            fill, reason = execution_proxy(market["trades"][outcome], decision, signal["price"], cfg, markup)
            if fill and fill["fill_timestamp"] >= min(market["resolution_timestamp"], market["close_timestamp"]):
                fill, reason = None, "market_closed_before_execution"
            attempt = {"strategy": strategy, "date": ds, "period": period, "condition_id": cid, "bucket": market["title"],
                       "outcome": outcome, "decision_timestamp": decision, "signal_timestamp": signal["timestamp"],
                       "signal_price": signal["price"], "model_probability": probability, "estimated_net_edge": edge,
                       "forecast_mu_f": mean if strategy == "forecast_probability_edge" else None,
                       "released_max_f": max_seen if strategy == "observed_dead_bucket_no" else None, "reason": reason or "proxy_fill"}
            attempts.append(attempt)
            if fill:
                settlement = market["yes_settlement"] if outcome == "Yes" else 1 - market["yes_settlement"]
                if fill["fill_timestamp"] >= market["resolution_timestamp"]:
                    raise ValueError("Fill after resolution")
                pnl = settlement * fill["shares"] - fill["cost"]
                day_pnl += pnl
                fills.append({**attempt, **fill, "settlement": settlement, "pnl": pnl,
                              "resolution_timestamp": market["resolution_timestamp"], "close_timestamp": market["close_timestamp"],
                              "settlement_source": market["settlement_source"],
                              "resolution_transaction_hash": market["resolution_transaction_hash"]})
            break
        daily.append({"date": ds, "period": period, "pnl": day_pnl, "attempted": attempted})
    return fills, attempts, daily


def summarize(fills, attempts, daily, cfg):
    count = len(fills)
    pnl = sum(t["pnl"] for t in fills)
    cost = sum(t["cost"] for t in fills)
    capital = cfg["initial_capital"]
    peak = capital
    drawdown = 0.0
    for fill in sorted(fills, key=lambda row: row["resolution_timestamp"]):
        capital += fill["pnl"]
        peak = max(peak, capital)
        drawdown = max(drawdown, (peak - capital) / peak)
    cash, reserved, min_cash, max_reserved = cfg["initial_capital"], 0.0, cfg["initial_capital"], 0.0
    ledger = []
    for fill in fills:
        ledger.append((fill["fill_timestamp"], 1, -fill["cost"], fill["cost"]))
        ledger.append((fill["resolution_timestamp"], 0, fill["settlement"] * fill["shares"], -fill["cost"]))
    for _, _, cash_change, reserve_change in sorted(ledger):
        cash += cash_change
        reserved += reserve_change
        min_cash, max_reserved = min(min_cash, cash), max(max_reserved, reserved)
        if cash < -1e-8 or reserved < -1e-8:
            raise ValueError("Cash conservation failure")
    if abs(cash - cfg["initial_capital"] - pnl) > 1e-7 or abs(reserved) > 1e-7:
        raise ValueError("Settlement ledger did not reconcile")
    daily_values = [d["pnl"] for d in daily]
    samples = []
    rng = random.Random(cfg["bootstrap_seed"])
    if daily_values and count:
        # Three-day blocks retain short weather-regime dependence.
        for _ in range(cfg["bootstrap_samples"]):
            draw = []
            while len(draw) < len(daily_values):
                start = rng.randrange(len(daily_values))
                draw.extend(daily_values[(start + j) % len(daily_values)] for j in range(3))
            samples.append(sum(draw[:len(daily_values)]))
        samples.sort()
    ci = [samples[int(len(samples) * .025)], samples[min(len(samples) - 1, int(len(samples) * .975))]] if samples else [None, None]
    return {"days": len(daily), "attempts": len(attempts), "fills": count, "wins": sum(t["settlement"] == 1 for t in fills),
            "net_pnl": round(pnl, 6), "capital_return_pct": round(pnl / cfg["initial_capital"] * 100, 6),
            "cost_roi_pct": round(pnl / cost * 100, 6) if cost else None, "deployed_cost": round(cost, 6),
            "fees": round(sum(t["fees"] for t in fills), 6), "settlement_equity_max_drawdown_pct": round(drawdown * 100, 6),
            "minimum_cash": round(min_cash, 6), "maximum_reserved_cost": round(max_reserved, 6), "ending_cash": round(cash, 6),
            "three_day_block_bootstrap_pnl_95pct": [round(v, 6) if v is not None else None for v in ci],
            "fill_model": "assumed execution at first subsequent observed BUY plus price markup",
            "attempt_outcomes": dict(Counter(t["reason"] for t in attempts)),
            "win_rate_pct": round(sum(t["settlement"] == 1 for t in fills) / count * 100, 6) if count else None}


def main():
    cfg = read_json(CONFIG)
    days, integrity = load_days(cfg, load_observations(cfg))
    fit = calibrate(days, cfg)
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "config": cfg,
              "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(), "calibration": fit,
              "requested_period": [cfg["start_date"], cfg["end_date"]], "measured_days": len(days),
              "integrity": integrity, "strategies": {}, "live_execution_verified": False,
              "limitations": ["Historical executable quotes/depth unavailable; fills and participation are assumptions.",
                              "Trade-proxy price is rounded up to 0.001; this is a modeling precision, not verified historical tick-size metadata.",
                              "IEM KORD routine NOAA reports match the station/hourly convention but do not prove historical vendor publication timing or revisions.",
                              "Forecast run initialization plus six hours is an availability assumption, not observed historical delivery.",
                              "Current 0.05 taker rate is applied as a current-cost scenario; historical fees are not independently reconstructed.",
                              "Only one city and 30 out-of-sample days; correlated buckets restricted to one attempted order per event.",
                              "Drawdown uses realized settlement timestamps; open positions remain at cost, not executable liquidation value."]}
    all_fills, all_attempts = [], []
    for strategy in ("forecast_probability_edge", "observed_dead_bucket_no"):
        scenarios = {}
        for markup in [cfg["base_price_markup"], *cfg["stress_price_markups"]]:
            fills, attempts, daily = run_strategy(days, cfg, fit, strategy, markup)
            scenario = {}
            for period in ("calibration_in_sample", "out_of_sample"):
                scenario[period] = summarize([t for t in fills if t["period"] == period], [t for t in attempts if t["period"] == period], [d for d in daily if d["period"] == period], cfg)
            scenarios[str(markup)] = scenario
            if markup == cfg["base_price_markup"]:
                all_fills.extend(fills)
                all_attempts.extend(attempts)
        oos = scenarios[str(cfg["base_price_markup"])]["out_of_sample"]
        verdict = "insufficient_out_of_sample_fills" if oos["fills"] < 20 else "not_supported"
        if oos["fills"] >= 20 and oos["three_day_block_bootstrap_pnl_95pct"][0] > 0 and all(s["out_of_sample"]["net_pnl"] > 0 for s in scenarios.values()):
            verdict = "positive_under_execution_assumptions"
        report["strategies"][strategy] = {"verdict": verdict, "scenarios": scenarios}
    report["actual_oos_date_range"] = [d["date"] for d in days if d["date"] > cfg["calibration_end"]][::29]
    output = ROOT / "research/weather_results"
    write_json(output / "report.json", report)
    write_json(output / "fills.json", all_fills)
    write_json(output / "attempts.json", all_attempts)
    fields = list(all_fills[0]) if all_fills else ["strategy", "date", "pnl"]
    with (output / "fills.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_fills)
    print(json.dumps({strategy: {"verdict": data["verdict"], **data["scenarios"][str(cfg["base_price_markup"])]["out_of_sample"]} for strategy, data in report["strategies"].items()}, indent=2))


if __name__ == "__main__":
    main()
