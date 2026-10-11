"""Exploratory whole-cohort S4 replay using distinct Yes/No historical L2 books.

The forecast fit and thresholds precede this follow-up. Its evaluation period was
already examined by the trade-proxy study, so this is not an untouched holdout.
Only local cached public data is read; no live orders or network calls are made.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math

from weather_backtest import bucket_probability, calibrate, load_days, load_observations, summarize
from weather_data import CONFIG, DATA, ROOT, read_json, write_json

BOOK_CONFIG = ROOT / "research/weather_full_book_config.json"
BOOK_MANIFEST = ROOT / "research/weather_full_book_data/manifest.json"
BOOK_SCOPE = ROOT / "research/weather_full_book_acquisition_config_v2.json"
SNAPSHOTS = ROOT / "data/weather_full_books/snapshots.jsonl"
OUTPUT = ROOT / "results/weather_full_books"


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def cost_for(price, cfg):
    shares, rate, p = (Decimal(str(v)) for v in (cfg["order_shares"], cfg["current_taker_fee_rate"], price))
    fee = float((shares * rate * p * (1 - p)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))
    return float(shares * p) + fee, fee


def book_reason(book, timestamp, max_age, shares):
    """Reject missing, nonfinite, future, stale, inconsistent or shallow books."""
    if book.get("timestamp") != timestamp:
        return "wrong_sample_time"
    if not book.get("book_book_valid"):
        return "invalid_depth_book"
    for key in ("book_ask", "book_ask_size", "book_quote_recv_ts", "book_quote_source_ts"):
        if book.get(key) is None or not math.isfinite(float(book[key])):
            return "missing_or_nonfinite_" + key
    received, source = book["book_quote_recv_ts"], book["book_quote_source_ts"]
    if not received < timestamp:
        return "quote_not_before_decision"
    if not 0 <= timestamp - received <= max_age:
        return "stale_received_quote"
    if not 0 <= timestamp - source <= max_age:
        return "stale_or_future_source_quote"
    bid = book.get("book_bid")
    if not 0 < book["book_ask"] < 1 or (bid is not None and (not math.isfinite(float(bid)) or not 0 <= bid < book["book_ask"])):
        return "invalid_spread"
    if book["book_ask_size"] < shares:
        return "insufficient_top_depth"
    return None


def run_scenario(days, snapshots, cfg, fit, max_age, markup):
    """Rank signal-time information only; one attempt/day, no fill-based rerank."""
    fills, attempts, daily, candidates = [], [], [], []
    cash, outstanding = cfg["initial_capital"], []
    for day in sorted(days, key=lambda value: value["date"]):
        ds = day["date"]
        decision = int(datetime.fromisoformat(ds).replace(hour=cfg["decision_utc_hour"], tzinfo=timezone.utc).timestamp())
        fill_time = decision + cfg["execution_latency_seconds"]
        remaining = []
        for position in outstanding:
            if position["resolution_timestamp"] <= fill_time:
                cash += position["payout"]
            else:
                remaining.append(position)
        outstanding = remaining
        mean = sum(fit["weights"][model] * day["model_maxes"][model] for model in fit["weights"]) + fit["bias_f"]
        eligible = []
        for market in day["markets"]:
            stop = min(market["close_timestamp"], market["resolution_timestamp"])
            yes_probability = bucket_probability(mean, fit["sigma_f"], market["bounds"])
            for outcome in ("Yes", "No"):
                snapshot = snapshots[(ds, market["condition_id"], outcome)]
                signal = snapshot["signal"]
                probability = yes_probability if outcome == "Yes" else 1 - yes_probability
                reason = "market_closed_before_signal" if decision >= stop else book_reason(signal, decision, max_age, cfg["order_shares"])
                modeled_price, edge = None, None
                if reason is None:
                    modeled_price = round(signal["book_ask"] + markup, 10)
                    if modeled_price >= 1:
                        reason = "signal_price_limit"
                    else:
                        cost, _ = cost_for(modeled_price, cfg)
                        edge = probability - cost / cfg["order_shares"]
                        if edge < cfg["forecast_min_net_edge"]:
                            reason = "insufficient_net_edge"
                candidate = {"date": ds, "condition_id": market["condition_id"], "bucket": market["title"],
                    "outcome": outcome, "token_id": snapshot["token_id"], "decision_timestamp": decision,
                    "signal_ask": signal.get("book_ask"), "signal_ask_size": signal.get("book_ask_size"),
                    "model_probability": probability, "forecast_mu_f": mean, "estimated_net_edge": edge,
                    "signal_reason": reason or "eligible", "selected": False}
                candidates.append(candidate)
                if reason is None:
                    eligible.append((candidate, market, snapshot))
        if not eligible:
            daily.append({"date": ds, "pnl": 0.0, "attempted": False})
            continue
        candidate, market, snapshot = max(eligible, key=lambda item: (item[0]["estimated_net_edge"], item[0]["condition_id"], item[0]["outcome"]))
        candidate["selected"] = True
        execution = snapshot["execution"]
        reason = "market_closed_before_execution" if fill_time >= min(market["close_timestamp"], market["resolution_timestamp"]) else book_reason(execution, fill_time, max_age, cfg["order_shares"])
        price, cost, fee = None, None, None
        if reason is None:
            price = round(execution["book_ask"] + markup, 10)
            if price >= 1 or price > candidate["signal_ask"] + cfg["max_price_move"] + 1e-10:
                reason = "execution_price_limit"
            else:
                cost, fee = cost_for(price, cfg)
                if cost > cash + 1e-10:
                    reason = "insufficient_cash"
        attempt = {**candidate, "reason": reason or "conditional_book_fill", "execution_ask": execution.get("book_ask"),
                   "execution_ask_size": execution.get("book_ask_size"), "fill_timestamp": fill_time}
        attempts.append(attempt)
        pnl = 0.0
        if reason is None:
            settlement = market["yes_settlement"] if candidate["outcome"] == "Yes" else 1 - market["yes_settlement"]
            payout = cfg["order_shares"] * settlement
            pnl = payout - cost
            fill = {**attempt, "entry_price": price, "adverse_price_markup": markup, "shares": cfg["order_shares"],
                "fees": fee, "cost": cost, "settlement": settlement, "payout": payout, "pnl": pnl,
                "close_timestamp": market["close_timestamp"], "resolution_timestamp": market["resolution_timestamp"],
                "resolution_transaction_hash": market["resolution_transaction_hash"], "settlement_source": market["settlement_source"],
                "cash_before_entry": cash, "cash_after_entry": cash - cost,
                "signal_quote_recv_ts": snapshot["signal"]["book_quote_recv_ts"],
                "signal_quote_source_ts": snapshot["signal"]["book_quote_source_ts"],
                "execution_quote_recv_ts": execution["book_quote_recv_ts"],
                "execution_quote_source_ts": execution["book_quote_source_ts"],
                "historical_tick_size": execution.get("book_tick_size")}
            fills.append(fill)
            outstanding.append(fill)
            cash -= cost
        daily.append({"date": ds, "pnl": pnl, "attempted": True})
    summary = summarize(fills, attempts, daily, cfg)
    summary["fill_model"] = "conditional historical top-ask depth after 60s plus adverse cost buffer"
    summary["candidate_outcomes"] = dict(Counter(c["signal_reason"] for c in candidates))
    summary["unknown_historical_tick_fills"] = sum(f["historical_tick_size"] is None for f in fills)
    if abs(cash + sum(f["payout"] for f in outstanding) - summary["ending_cash"]) > 1e-7:
        raise ValueError("Runtime cash reservation and independent settlement ledger disagree")
    return {"summary": summary, "fills": fills, "attempts": attempts, "daily": daily, "candidates": candidates}


def load_inputs(cfg):
    baseline = read_json(CONFIG)
    if sha256(CONFIG) != cfg["baseline_config_sha256"]:
        raise ValueError("Frozen baseline configuration changed")
    for key in ("execution_latency_seconds", "forecast_min_net_edge", "order_shares", "current_taker_fee_rate", "max_price_move"):
        if cfg[key] != baseline[key]:
            raise ValueError(f"Full-book rule differs from frozen baseline: {key}")
    if cfg["decision_utc_hour"] < baseline["forecast_run_utc_hour"] + baseline["forecast_release_lag_hours"]:
        raise ValueError("Forecast not yet released under frozen lag assumption")
    days, integrity = load_days(baseline, load_observations(baseline))
    fit = calibrate(days, baseline)
    if fit != read_json(ROOT / "research/weather_results/report.json")["calibration"]:
        raise ValueError("Calibration differs from original study")
    days = [d for d in days if cfg["start_date"] <= d["date"] <= cfg["end_date"]]
    if len(days) != cfg["expected_days"] or any(len(d["markets"]) != cfg["expected_markets_per_day"] for d in days):
        raise ValueError("Required 30-day, 330-market scope is incomplete")
    manifest = read_json(BOOK_MANIFEST)
    if not manifest.get("complete") or manifest.get("errors"):
        raise ValueError("Full book collection is incomplete")
    if manifest["scope_sha256"] != sha256(BOOK_SCOPE):
        raise ValueError("Full book acquisition must use the corrected v2 reconstruction")
    if (manifest["completed_days"] != 30 or manifest["market_count"] != 330 or manifest["outcome_snapshots"] != 660
            or (ROOT / manifest["snapshots_path"]).resolve() != SNAPSHOTS.resolve()
            or manifest["snapshots_sha256"] != sha256(SNAPSHOTS)):
        raise ValueError("Full book manifest scope or hash mismatch")
    rows = [json.loads(line) for line in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if line]
    expected, snapshots = {}, {}
    for day in days:
        event = read_json(DATA / "events" / f"{day['date']}.json")["body"][0]
        for market in event["markets"]:
            for outcome, token in zip(json.loads(market["outcomes"]), json.loads(market["clobTokenIds"])):
                expected[(day["date"], market["conditionId"], outcome)] = str(token)
    for row in rows:
        key = (row["date"], row["condition_id"], row["outcome"])
        if key in snapshots or key not in expected or expected[key] != str(row["token_id"]):
            raise ValueError("Duplicate, unknown, or incorrectly mapped outcome snapshot")
        decision = int(datetime.fromisoformat(row["date"]).replace(hour=cfg["decision_utc_hour"], tzinfo=timezone.utc).timestamp())
        if row["decision_timestamp"] != decision or row["signal"]["timestamp"] != decision or row["execution"]["timestamp"] != decision + cfg["execution_latency_seconds"]:
            raise ValueError("Snapshot timing differs from frozen decision and delay")
        snapshots[key] = row
    if snapshots.keys() != expected.keys() or len(rows) != 660:
        raise ValueError("Full cohort requires all 660 distinct outcome snapshots")
    return days, snapshots, fit, integrity


def main():
    cfg = read_json(BOOK_CONFIG)
    days, snapshots, fit, integrity = load_inputs(cfg)
    scenarios = {}
    for max_age in (cfg["primary_max_quote_age_seconds"], *cfg["sensitivity_max_quote_age_seconds"]):
        for markup in (cfg["base_price_markup"], *cfg["stress_price_markups"]):
            key = f"age_{max_age}_markup_{markup}"
            result = run_scenario(days, snapshots, cfg, fit, max_age, markup)
            write_json(OUTPUT / f"{key}.json", result)
            scenarios[key] = {"max_quote_age_seconds": max_age, "price_markup": markup,
                              "result_file": f"{key}.json", "result_sha256": sha256(OUTPUT / f"{key}.json"), **result["summary"]}
    primary_key = f"age_{cfg['primary_max_quote_age_seconds']}_markup_{cfg['base_price_markup']}"
    primary = scenarios[primary_key]
    # Descriptive evidence only: this reuses a previously inspected period.
    verdict = "insufficient_conditional_fills" if primary["fills"] < 20 else "conditional_candidate_not_supported"
    if primary["fills"] >= 20 and primary["three_day_block_bootstrap_pnl_95pct"][0] > 0 and all(s["net_pnl"] > 0 for s in scenarios.values() if s["max_quote_age_seconds"] == cfg["primary_max_quote_age_seconds"]):
        verdict = "positive_exploratory_conditional_candidate"
    paths = [BOOK_CONFIG, CONFIG, BOOK_MANIFEST, SNAPSHOTS, DATA / "manifest.json", DATA / "observations_hourly.json",
             ROOT / "weather_full_book_backtest.py", ROOT / "weather_backtest.py", ROOT / "weather_data.py",
             ROOT / "weather_full_book_data.py", ROOT / "weather_book_check.py", ROOT / "btc_data.py",
             ROOT / "research/weather_full_book_acquisition_config.json",
             BOOK_SCOPE,
             ROOT / "research/weather_results/report.json"]
    for folder in ("events", "forecasts", "resolutions"):
        paths.extend(sorted((DATA / folder).glob("*.json")))
    inputs = [{"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)} for path in paths]
    write_json(OUTPUT / "input_manifest.json", {"files": inputs})
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "study_type": cfg["study_type"],
        "strategy": "forecast_probability_edge", "config": cfg, "config_sha256": sha256(BOOK_CONFIG),
        "input_manifest_sha256": sha256(OUTPUT / "input_manifest.json"), "data_sha256": sha256(SNAPSHOTS),
        "calibration": fit, "days": len(days), "markets": len(snapshots) // 2, "outcome_snapshots": len(snapshots),
        "date_range": [days[0]["date"], days[-1]["date"]], "primary_scenario": primary_key,
        "verdict": verdict, "live_execution_verified": False, "untouched_holdout": False, "scenarios": scenarios,
        "baseline_observation_settlement_mismatches": integrity["observation_settlement_mismatches"],
        "limitations": [
            "Exploratory follow-up selected after the same dates' trade-proxy results and 20-attempt L2 audit were examined; no untouched holdout remains.",
            "Book archive receipt timestamps are observed for the archive collector, not a trading bot; feed continuity between archived events is assumed.",
            "Five shares must exist at the actual outcome's top ask at both signal and delayed execution; displayed liquidity is not a guarantee of live fills.",
            "Adverse ask markups are modeled costs, not literal tick-valid limit orders. Historical initial tick/minimum-order metadata may be missing; current five-share size is assumed.",
            "Current 0.05 taker rate is a counterfactual current-cost scenario; historical fee schedules are not reconstructed.",
            "Cost and age scenarios rerun the frozen decision rule and may select different orders; higher stress PnL is not improved performance of a fixed portfolio.",
            "Forecast run plus six-hour availability and IEM historical observation publication are assumptions; forecast grid differs from station point.",
            "Cash is reserved until official chain resolution; drawdown marks open positions at cost, not at executable liquidation value.",
            "One city and 30 correlated event days; three-day block bootstrap intervals do not account for strategy selection or additional model risk."
        ]}
    write_json(OUTPUT / "summary.json", report)
    print(json.dumps({"verdict": verdict, "primary_scenario": primary_key, "scenarios": {key: {k: value[k] for k in ("attempts", "fills", "wins", "net_pnl", "fees", "three_day_block_bootstrap_pnl_95pct")} for key, value in scenarios.items()}}, indent=2))


if __name__ == "__main__":
    main()
