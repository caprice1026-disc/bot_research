"""Causal, small-size taker replay of recorded BTC 5m books; never sends orders.

Research adaptations of the cited PMBT snapshot/late-favorite/complete-set
strategies and polybot freshness/inventory rules. See references/SOURCE_AUDIT.md.
No maker fills, rebates or instantaneous two-leg execution are assumed.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STRATEGIES = ("complete_set", "btc_fair_value", "late_favorite")


def fee(shares: float, price: float, rate: float) -> float:
    if not all(math.isfinite(x) for x in (shares, price, rate)):
        raise ValueError("nonfinite fee input")
    if shares < 0 or not 0 <= price <= 1 or not 0 <= rate <= 1:
        raise ValueError("invalid fee input")
    c, p, r = (Decimal(str(x)) for x in (shares, price, rate))
    return float((c * r * p * (1 - p)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))


def get_json(url: str) -> object:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
            with opener.open(request, timeout=35) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 3:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def number(row: dict, key: str, default: float = math.nan) -> float:
    try:
        return float(row[key])
    except (KeyError, ValueError, TypeError):
        return default


def truth(value: object) -> bool:
    return str(value).lower() in ("true", "1")


def quote_valid(row: dict, side: str, config: dict) -> bool:
    bid, ask = number(row, f"{side}_bid"), number(row, f"{side}_ask")
    snapshot_limit = config.get("max_book_snapshot_age_seconds")
    if snapshot_limit is not None and not 0 <= number(row, f"{side}_book_snapshot_age_seconds") <= snapshot_limit:
        return False
    return (truth(row.get(f"{side}_book_valid"))
            and 0 < bid <= ask < 1
            and ask - bid <= config["max_spread"] + 1e-12
            and 0 <= number(row, f"{side}_quote_age_seconds") <= config["max_quote_age_seconds"]
            and 0 <= number(row, "decision_ts") - number(row, f"{side}_quote_source_ts") <= config["max_source_age_seconds"]
            and number(row, f"{side}_ask_size") >= config["min_shares"])


def reference(row: dict, config: dict) -> float | None:
    source = config.get("reference_source", "coinbase")
    value = number(row, f"{source}_price")
    age = number(row, f"{source}_age_seconds")
    source_age = number(row, "decision_ts") - number(row, f"{source}_source_ts")
    if (math.isfinite(value) and value > 0 and 0 <= age <= config["max_reference_age_seconds"]
            and 0 <= source_age <= config["max_source_age_seconds"]):
        return value
    return None


def fetch_settlements(rows: list[dict], output: Path) -> dict:
    """Winner flags from the official CLOB endpoint, used only after entry."""
    output.mkdir(parents=True, exist_ok=True)
    result, failures = {}, {}
    for slug in sorted({row["slug"] for row in rows}):
        path = output / f"{slug}.json"
        try:
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
            else:
                gamma_url = "https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"slug": slug})
                archive_metadata = ROOT / "data/expanded_books/metadata" / f"{slug}.json"
                if archive_metadata.exists():
                    cached = json.loads(archive_metadata.read_text(encoding="utf-8"))
                    gamma_url = cached["url"]
                    markets = [m for e in cached["body"] for m in e.get("markets", []) if m.get("slug") == slug]
                else:
                    markets = get_json(gamma_url)
                if markets == []:
                    gamma_url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({"slug": slug})
                    events = get_json(gamma_url)
                    markets = [m for e in events for m in e.get("markets", []) if m.get("slug") == slug]
                if not isinstance(markets, list) or len(markets) != 1 or markets[0]["slug"] != slug:
                    raise ValueError("market lookup must uniquely match slug")
                market = markets[0]
                clob_url = f"https://clob.polymarket.com/markets/{market['conditionId']}"
                clob = get_json(clob_url)
                record = {"retrieved_utc": datetime.now(timezone.utc).isoformat(),
                          "urls": [gamma_url, clob_url], "gamma": market, "clob": clob}
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            clob = record["clob"]
            tokens = clob.get("tokens", [])
            if not clob.get("closed") or len(tokens) != 2 or sum(bool(t.get("winner")) for t in tokens) != 1:
                raise ValueError("not a settled binary with one verified winner")
            winner = next(t for t in tokens if t.get("winner"))
            row_tokens = {str(row[f"{side}_token_id"]) for row in rows if row["slug"] == slug for side in ("up", "down")}
            if {str(t["token_id"]) for t in tokens} != row_tokens:
                raise ValueError("settlement token identifiers disagree with recorded market")
            result[slug] = {"winner_token": str(winner["token_id"]),
                            "winner_outcome": winner["outcome"],
                            "closed_time": record["gamma"].get("closedTime"),
                            "fee_schedule": record["gamma"].get("feeSchedule"),
                            "min_order_size": clob.get("minimum_order_size", 5),
                            "tick_size": clob.get("minimum_tick_size", 0.01)}
        except Exception as exc:
            failures[slug] = f"{type(exc).__name__}: {exc}"
    (output / "manifest.json").write_text(json.dumps({"settled": result, "failures": failures}, indent=2), encoding="utf-8")
    return result


def past_row(history: list[dict], timestamps: list[float], ts: float, max_age: float = 2) -> dict | None:
    index = bisect.bisect_right(timestamps, ts) - 1
    if index < 0 or ts - timestamps[index] > max_age:
        return None
    return history[index]


def fair_probability(history: list[dict], timestamps: list[float], row: dict, config: dict) -> float | None:
    """Driftless estimate of the final 60s TWAP, anchored to received oracle.

    The pre-open Chainlink TWAP is a strike proxy (up to 3s old), not a future
    Gamma priceToBeat field. Coinbase supplies volatility; the current oracle
    versus trailing Coinbase average supplies a known-at-decision basis.
    """
    ts = number(row, "decision_ts")
    start = number(row, "window_start_ts")
    opening = past_row(history, timestamps, start)
    if opening is None:
        return None
    current = reference(row, config)
    window = config["twap_window_seconds"]

    def trailing_spot(at: float) -> list[float] | None:
        sample = history[bisect.bisect_right(timestamps, at - window):bisect.bisect_right(timestamps, at)]
        values = [reference(r, config) for r in sample]
        if len(values) != window or any(p is None for p in values):
            return None
        if any(number(b, "decision_ts") - number(a, "decision_ts") > 1.5 for a, b in zip(sample, sample[1:])):
            return None
        return values

    def oracle(r: dict) -> float | None:
        if config.get("oracle_mode") == "spot_twap_proxy":
            values = trailing_spot(number(r, "decision_ts"))
            return statistics.mean(values) if values else None
        age = number(r, "decision_ts") - number(r, "chainlink60_source_ts")
        value = number(r, "chainlink60_price")
        if 0 <= age <= config["max_source_age_seconds"] and value > 0:
            return value
        return None

    initial, current_oracle = oracle(opening), oracle(row)
    if initial is None or current is None or current_oracle is None:
        return None
    returns = []
    lo = bisect.bisect_left(timestamps, ts - config["volatility_window_seconds"])
    for prev, nxt in zip(history[lo:], history[lo + 1:]):
        if number(nxt, "decision_ts") > ts:
            break
        dt = number(nxt, "decision_ts") - number(prev, "decision_ts")
        p, q = reference(prev, config), reference(nxt, config)
        if 0 < dt <= 1.5 and p and q:
            returns.append(math.log(q / p) / math.sqrt(dt))
    if len(returns) < config["volatility_min_samples"]:
        return None
    sigma = max(statistics.stdev(returns), config["volatility_floor_per_sqrt_second"])
    tte = number(row, "window_end_ts") - ts
    if tte <= 0:
        return None
    recent_rows = [r for r in history[bisect.bisect_right(timestamps, ts - window):]
                   if number(r, "decision_ts") <= ts]
    recent = [reference(r, config) for r in recent_rows]
    if len(recent) < window or any(p is None for p in recent):
        return None
    # Require contiguous actual seconds, not an interpolated spot tape.
    if any(number(b, "decision_ts") - number(a, "decision_ts") > 1.5 for a, b in zip(recent_rows, recent_rows[1:])):
        return None
    basis = current_oracle / statistics.mean(recent)
    if tte >= window:
        expected = current * basis
        variance_time = tte - 2 * window / 3
    else:
        known = [reference(r, config) for r in recent_rows if number(r, "decision_ts") > ts + tte - window]
        expected = (statistics.mean(known) * (window - tte) + current * tte) * basis / window
        variance_time = tte ** 3 / (3 * window ** 2)
    z = (expected - initial) / (current * basis * sigma * math.sqrt(variance_time))
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def signal(strategy: str, history: list[dict], times: list[float], row: dict, config: dict) -> tuple[list[str], float] | None:
    ts, end = number(row, "decision_ts"), number(row, "window_end_ts")
    tte = end - ts
    sides = [side for side in ("up", "down") if quote_valid(row, side, config)]
    if strategy == "complete_set":
        if len(sides) != 2 or tte < 10:
            return None
        cost = sum(number(row, f"{side}_ask") + config["slippage_per_share"]
                   + fee(1, min(1, number(row, f"{side}_ask") + config["slippage_per_share"]), config["fee_rate_current"])
                   for side in sides)
        if 1 - cost >= config["pair_net_edge"]:
            return sides, 1 - cost
    elif strategy == "btc_fair_value":
        if not config["fair_value_min_tte"] <= tte <= config["fair_value_max_tte"]:
            return None
        probability = fair_probability(history, times, row, config)
        if probability is None:
            return None
        for side, p in (("up", probability), ("down", 1 - probability)):
            ask = number(row, f"{side}_ask")
            if side not in sides or not config["fair_value_min_price"] <= ask <= config["fair_value_max_price"]:
                continue
            price = min(1, ask + config["slippage_per_share"])
            edge = p - price - fee(1, price, config["fee_rate_current"])
            if edge >= config["fair_value_net_edge"]:
                return [side], edge
    elif strategy == "late_favorite":
        if not config["late_favorite_min_tte"] <= tte <= config["late_favorite_max_tte"]:
            return None
        prev = past_row(history, times, ts - config["late_favorite_momentum_seconds"])
        opening = past_row(history, times, number(row, "window_start_ts"))
        if prev is None or prev.get("slug") != row["slug"] or opening is None:
            return None
        initial, current = reference(opening, config), reference(row, config)
        if initial is None or current is None:
            return None
        for side, direction in (("up", 1), ("down", -1)):
            if side not in sides or not quote_valid(prev, side, config):
                continue
            bid, ask = number(row, f"{side}_bid"), number(row, f"{side}_ask")
            if (bid >= config["late_favorite_min_bid"] and ask <= config["late_favorite_max_ask"]
                    and bid - number(prev, f"{side}_bid") >= config["late_favorite_min_price_change"]
                    and direction * (current - initial) > 0):
                return [side], bid - number(prev, f"{side}_bid")
    else:
        raise ValueError(strategy)
    return None


def execute_leg(row: dict, side: str, limit: float, shares: float, config: dict) -> dict | None:
    if not quote_valid(row, side, config) or number(row, f"{side}_ask_size") < shares:
        return None
    price = number(row, f"{side}_ask") + config["slippage_per_share"]
    if not 0 < price < 1 or price > limit + 1e-12:
        return None
    return {"side": side, "token": row[f"{side}_token_id"], "price": price,
            "shares": shares, "fee": fee(shares, price, config["fee_rate_current"]),
            "fill_ts": number(row, "decision_ts")}


def replay(rows: list[dict], settlements: dict, strategy: str, config: dict) -> tuple[list[dict], dict]:
    history, times, trades = [], [], []
    attempted, diagnostics = set(), Counter()
    pending, open_trades = None, []
    cash = float(config["initial_cash"])

    def record_position(order: dict) -> None:
        if not order["legs"]:
            return
        settlement = settlements[order["slug"]]
        legs = order["legs"]
        cost = sum(l["price"] * l["shares"] + l["fee"] for l in legs)
        payout = sum(l["shares"] for l in legs if l["token"] == settlement["winner_token"])
        try:
            closed_ts = datetime.fromisoformat(settlement["closed_time"].replace("Z", "+00:00")).timestamp()
        except (KeyError, AttributeError, ValueError, TypeError):
            closed_ts = order["end_ts"]
        trade = {"strategy": strategy, "slug": order["slug"], "signal_ts": order["signal_ts"],
                 "fill_ts": min(l["fill_ts"] for l in legs), "legs": legs,
                 "cost": cost, "payout": payout, "pnl": payout - cost,
                 "fees": sum(l["fee"] for l in legs),
                 "partial_pair": len(legs) < len(order["sides"]),
                 "cash_release_ts": max(closed_ts, order["end_ts"]) + config["settlement_cash_delay_seconds"],
                 "cash_after_entry": cash, "signal_value": order["edge"]}
        trades.append(trade)
        open_trades.append(trade)

    for row in rows:
        ts, slug = number(row, "decision_ts"), row["slug"]
        for trade in list(open_trades):
            if ts >= trade["cash_release_ts"]:
                cash += trade["payout"]
                open_trades.remove(trade)
        history.append(row)
        times.append(ts)
        if pending and ts >= pending["due"]:
            if slug != pending["slug"] or ts > pending["due"] + 1.1:
                diagnostics["pending_expired_or_gap"] += 1
                record_position(pending)
                pending = None
            else:
                leg = execute_leg(row, pending["sides"][len(pending["legs"])],
                                  pending["limits"][len(pending["legs"])], pending["shares"], config)
                if leg:
                    cost = leg["price"] * leg["shares"] + leg["fee"]
                    if cost > cash + 1e-9:
                        leg = None
                    else:
                        cash -= cost
                        pending["legs"].append(leg)
                if leg and len(pending["legs"]) < len(pending["sides"]):
                    # Second leg arrives after its own latency; it is not atomic.
                    pending["due"] = ts + config["latency_seconds"]
                    continue
                if pending["legs"]:
                    record_position(pending)
                else:
                    diagnostics["unfilled"] += 1
                pending = None
        if pending or slug in attempted:
            continue
        if slug not in settlements:
            diagnostics["missing_settlement_rows"] += 1
            continue
        if ts >= number(row, "window_end_ts") or ts < number(row, "window_start_ts"):
            continue
        candidate = signal(strategy, history, times, row, config)
        if candidate is None:
            continue
        sides, edge = candidate
        limits = [number(row, f"{s}_ask") + config["slippage_per_share"] for s in sides]
        unit_cost = sum(p + fee(1, min(p, 1), config["fee_rate_current"]) for p in limits)
        shares = min(config["max_shares"], math.floor(min(cash, config["max_cost_per_market"]) / unit_cost * 100) / 100)
        while shares > 0 and sum(shares * p + fee(shares, min(p, 1), config["fee_rate_current"]) for p in limits) > min(cash, config["max_cost_per_market"]) + 1e-10:
            shares = round(shares - .01, 2)
        minimum = max(config["min_shares"], float(settlements[slug].get("min_order_size", 5)))
        if shares < minimum:
            diagnostics["cash_or_minimum_size"] += 1
            continue
        attempted.add(slug)
        diagnostics["signals"] += 1
        pending = {"slug": slug, "signal_ts": ts, "due": ts + config["latency_seconds"],
                   "sides": sides, "limits": limits, "shares": shares, "legs": [], "edge": edge,
                   "end_ts": number(row, "window_end_ts")}
    if pending and pending["legs"]:
        # Never erase a bought first leg when data ends before the hedge.
        record_position(pending)
    diagnostics["attempted_markets"] = len(attempted)
    diagnostics["ending_cash_before_final_settlement"] = round(cash, 6)
    return trades, dict(diagnostics)


def summary(trades: list[dict], initial: float) -> dict:
    pnl = sum(t["pnl"] for t in trades)
    cost = sum(t["cost"] for t in trades)
    equity, peak, drawdown = initial, initial, 0.0
    # This is settlement-order drawdown, not unobserved intratrade NAV.
    for trade in sorted(trades, key=lambda t: t["cash_release_ts"]):
        equity += trade["pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"trades": len(trades), "markets": len({t["slug"] for t in trades}),
            "cost": round(cost, 6), "net_pnl": round(pnl, 6),
            "return_on_initial_cash": round(pnl / initial, 6),
            "roi_on_deployed_cost": round(pnl / cost, 6) if cost else None,
            "fees": round(sum(t["fees"] for t in trades), 6),
            "wins": sum(t["pnl"] > 0 for t in trades),
            "partial_pairs": sum(t["partial_pair"] for t in trades),
            "settlement_order_max_drawdown": round(drawdown, 6)}


def fixed_fill_stress(trades: list[dict], extra_slippage: float, rate: float) -> dict:
    """Hold the selected trades fixed; adverse repricing cannot cherry-pick fills."""
    extra_cost = sum(l["shares"] * (min(1.0, l["price"] + extra_slippage) - l["price"])
                     + fee(l["shares"], min(1.0, l["price"] + extra_slippage), rate) - l["fee"]
                     for t in trades for l in t["legs"])
    return {"extra_slippage_per_share": extra_slippage,
            "net_pnl": round(sum(t["pnl"] for t in trades) - extra_cost, 6),
            "trade_count": len(trades)}


def day_bootstrap(trades: list[dict], dates: list[str]) -> dict:
    if not trades:
        return {"days": len(dates), "mean_daily_pnl_ci95": None, "reason": "no_executed_trades"}
    daily = dict.fromkeys(dates, 0.0)
    for t in trades:
        day = datetime.fromtimestamp(t["signal_ts"], timezone.utc).date().isoformat()
        daily[day] += t["pnl"]
    values = list(daily.values())
    if len(values) < 10:
        return {"days": len(values), "mean_daily_pnl_ci95": None, "reason": "fewer_than_10_days"}
    rng = random.Random(731)
    means = []
    for _ in range(2000):
        draw = []
        while len(draw) < len(values):
            start = rng.randrange(len(values))
            draw.extend(values[(start + i) % len(values)] for i in range(3))
        means.append(statistics.mean(draw[:len(values)]))
    means.sort()
    return {"days": len(values), "mean_daily_pnl_ci95": [means[49], means[1949]],
            "method": "fixed-seed circular three-UTC-day block bootstrap, 2000 draws; exploratory after testing five candidates"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "data/btc_replay.csv")
    parser.add_argument("--config", type=Path, default=ROOT / "btc_config.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results/btc")
    parser.add_argument("--reference-source", choices=("coinbase", "binance"))
    parser.add_argument("--oracle-mode", choices=("recorded_chainlink60", "spot_twap_proxy"))
    parser.add_argument("--max-book-snapshot-age", type=float)
    args = parser.parse_args()
    with args.data.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    rows.sort(key=lambda r: (number(r, "decision_ts"), r["slug"]))
    if not rows or len({number(r, "decision_ts") for r in rows}) != len(rows):
        raise ValueError("requires exactly one active five-minute market per second")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.reference_source:
        config["reference_source"] = args.reference_source
    if args.oracle_mode:
        config["oracle_mode"] = args.oracle_mode
    if args.max_book_snapshot_age is not None:
        config["max_book_snapshot_age_seconds"] = args.max_book_snapshot_age
    settlements = fetch_settlements(rows, ROOT / "data/btc_settlements")
    args.output.mkdir(parents=True, exist_ok=True)
    midpoint = (number(rows[0], "decision_ts") + number(rows[-1], "decision_ts")) / 2
    report = {"data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
              "config": config, "rows": len(rows), "markets": len({r["slug"] for r in rows}),
              "settled_markets": len(settlements),
              "start_utc": datetime.fromtimestamp(number(rows[0], "decision_ts"), timezone.utc).isoformat(),
              "end_utc": datetime.fromtimestamp(number(rows[-1], "decision_ts"), timezone.utc).isoformat(),
              "days": len({datetime.fromtimestamp(number(r, "decision_ts"), timezone.utc).date() for r in rows}),
              "split_ts": midpoint, "strategies": {},
              "execution_scope": "recorded visible depth, delayed taker fills with adverse price buffer; hypothetical queue/latency/market impact; no live orders",
              "fee_scope": "current-fee counterfactual on historical books, not a claim of historical actual charged fee",
              "drawdown_scope": "settlement-order equity; intratrade mark-to-market drawdown not established"}
    for strategy in STRATEGIES:
        result = {}
        for name, latency, slippage in (("base", 1, 0.01), ("stress", 2, 0.02)):
            cfg = dict(config, latency_seconds=latency, slippage_per_share=slippage)
            trades, diagnostics = replay(rows, settlements, strategy, cfg)
            result[name] = {"all": summary(trades, cfg["initial_cash"]),
                            "first_half": summary([t for t in trades if t["signal_ts"] < midpoint], cfg["initial_cash"]),
                            "second_half": summary([t for t in trades if t["signal_ts"] >= midpoint], cfg["initial_cash"]),
                            "diagnostics": diagnostics}
            if name == "base":
                holdout = [t for t in trades if t["signal_ts"] >= midpoint]
                holdout_dates = sorted({datetime.fromtimestamp(number(r, "decision_ts"), timezone.utc).date().isoformat()
                                        for r in rows if number(r, "decision_ts") >= midpoint})
                result["fixed_fill_cost_stress_all"] = fixed_fill_stress(trades, .01, cfg["fee_rate_current"])
                result["fixed_fill_cost_stress_holdout"] = fixed_fill_stress(holdout, .01, cfg["fee_rate_current"])
                result["holdout_day_bootstrap"] = day_bootstrap(holdout, holdout_dates)
            (args.output / f"{strategy}_{name}_trades.json").write_text(json.dumps(trades, indent=2, allow_nan=False), encoding="utf-8")
        holdout = result["base"]["second_half"]
        ci = result["holdout_day_bootstrap"]["mean_daily_pnl_ci95"]
        if report["days"] < config["min_days_for_inference"] or report["markets"] < config["min_markets_for_inference"]:
            result["status"] = "insufficient_data"
        elif not result["base"]["all"]["trades"]:
            result["status"] = "no_executable_opportunities_in_sample"
        elif holdout["trades"] < config["min_holdout_trades"]:
            result["status"] = "insufficient_holdout_trades"
        elif holdout["net_pnl"] <= 0:
            result["status"] = "rejected_negative_holdout"
        elif result["stress"]["second_half"]["net_pnl"] <= 0 or result["fixed_fill_cost_stress_holdout"]["net_pnl"] <= 0:
            result["status"] = "rejected_cost_or_latency_sensitivity"
        elif ci is None or ci[0] <= 0:
            result["status"] = "inconclusive_holdout_uncertainty"
        else:
            result["status"] = "candidate_requires_forward_validation"
        if result["base"]["all"]["trades"] and result["base"]["all"]["net_pnl"] < 0:
            result["observed_result"] = "negative_in_observed_sample"
        elif not result["base"]["all"]["trades"]:
            result["observed_result"] = "no_executable_trades"
        else:
            result["observed_result"] = "positive_in_observed_sample_only"
        report["strategies"][strategy] = result
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"rows": report["rows"], "markets": report["markets"], "results": report["strategies"]}, indent=2))


if __name__ == "__main__":
    main()
