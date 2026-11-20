"""Untouched later weather days and fixed-rule forecast-provider comparison.

The original fit, thresholds and results are immutable. All network activity is
confined to collect/books; evaluate and verify use local public data only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / ".probe-deps"))
import requests
import certifi

# Existing archive readers target Python >=3.11. Keep compatibility local to
# this process, without changing their frozen source files or manifests.
if not hasattr(hashlib, "file_digest"):
    def _file_digest(handle, algorithm):
        digest = hashlib.new(algorithm)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest
    hashlib.file_digest = _file_digest

import weather_deep_research as deep
from weather_data import read_json, write_json, dates_between
from weather_backtest import calibrate, bucket_probability, summarize
from weather_full_book_backtest import run_scenario, book_reason

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "research/weather_followup_20261012/config.json"
DATA = ROOT / "data/weather_followup_20261012"
OUTPUT = ROOT / "results/weather_followup_20261012"
PRIOR = ROOT / "results/weather_deep/summary.json"
PRIOR_CONTROL = ROOT / "results/weather_deep/persistence_control.json"


def subset_current_provenance(con, city, ds, hour, conditions, start, end):
    from weather_archive_ranges import extract
    condition_hash = hashlib.sha256(json.dumps(sorted(conditions)).encode()).hexdigest()[:12]
    folder = DATA / city / "books"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ds}T{hour:02d}-{condition_hash}-{start}-{end}.parquet"
    meta_path = path.with_suffix(".manifest.json")
    if path.exists() and meta_path.exists():
        record = read_json(meta_path)
        if digest(path) != record["subset_sha256"]:
            raise ValueError("Cached archive subset changed")
        return path, record
    url = f"https://dl.pendulumflow.com/v3/{ds}/{hour:02d}/{ds}T{hour:02d}.parquet"
    temporary = path.with_suffix(".parquet.part")
    info = extract(url, conditions, start, end, temporary, DATA / "archive_ranges")
    temporary.replace(path)
    record = {**info, "source_url": url, "source_full_hash_verified": False, "source_published_hash": None,
              "source_manifest_status": "Not requested; local subset and cached ranges hashed", "condition_ids": sorted(conditions),
              "start": start, "end": end, "subset_sha256": digest(path), "subset_bytes": path.stat().st_size,
              "subset_path": str(path.relative_to(ROOT))}
    write_json(meta_path, record)
    return path, record


def fetch_verified(url, path, *, text=False, trim_trades=False):
    """Use the current certifi trust store, never disable TLS verification."""
    path = Path(path)
    if path.exists():
        record = read_json(path)
        if record["url"] != url:
            raise ValueError("Cached URL differs: " + str(path))
        return record["body"]
    for attempt in range(3):
        try:
            response = requests.get(url, headers={"User-Agent": "PolymarketWeatherResearch/1.0"}, timeout=45, verify=certifi.where())
            response.raise_for_status()
            body = response.text if text else response.json()
            if trim_trades:
                raise ValueError("This runner does not collect trade-proxy data")
            write_json(path, {"url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                              "response_sha256": hashlib.sha256(response.content).hexdigest(), "body": body})
            return body
        except requests.RequestException as exc:
            if isinstance(exc, requests.HTTPError) and response.status_code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def configure():
    # Only this process is redirected; the original entry points stay intact.
    deep.CONFIG, deep.DATA, deep.OUTPUT = CONFIG, DATA, OUTPUT
    deep.fetch = fetch_verified
    deep.subset = subset_current_provenance
    cfg = read_json(CONFIG)
    count = sum(len(list(dates_between(p["evaluation_start"], p["evaluation_end"]))) for p in cfg["profiles"])
    if count != cfg["expected_new_city_days"]:
        raise ValueError("City-day scope mismatch")
    if any(p["evaluation_start"] <= "2026-10-03" for p in cfg["profiles"]):
        raise ValueError("Later evaluation overlaps the inspected prior sample")
    if cfg["calibration_end"] >= min(p["evaluation_start"] for p in cfg["profiles"]):
        raise ValueError("Calibration overlaps evaluation")
    for p in cfg["profiles"]:
        if cfg["forecast_run_utc_hour"] + cfg["forecast_release_lag_hours"] > cfg["decision_local_hour"] - p["utc_offset_hours"]:
            raise ValueError("Forecast not released before decision")
    return cfg


def observed_highs(profile, with_coverage=False):
    offset = timezone(timedelta(hours=profile["utc_offset_hours"]))
    values = defaultdict(list)
    hours = defaultdict(set)
    raw = read_json(DATA / profile["city"] / "observations.json")["body"]
    for row in csv.DictReader(io.StringIO(raw)):
        if row["station"] != profile["iem_station"] or not row["metar"].startswith(profile["station"] + " ") or row["tmpf"] in ("M", ""):
            continue
        valid = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        value = float(row["tmpf"])
        if not math.isfinite(value) or not -50 < value < 130:
            raise ValueError("Invalid observed temperature")
        local = valid.astimezone(offset)
        values[local.date().isoformat()].append(value)
        hours[local.date().isoformat()].add(local.hour)
    highs = {ds: math.floor(max(v) + .5) for ds, v in values.items()}
    issues = [{"date": ds, "observation_hours": len(v)} for ds, v in hours.items() if len(v) != 24]
    return (highs, issues) if with_coverage else highs


def persistence_rows(days, highs):
    rows = []
    for day in days:
        at = date.fromisoformat(day["date"])
        previous = [(at - timedelta(days=i)).isoformat() for i in range(1, 8)]
        if any(ds not in highs for ds in previous):
            raise ValueError("Missing prior-seven-day observation history")
        rows.append({**day, "model_maxes": {"persistence": statistics.mean(highs[ds] for ds in previous)}})
    return rows


def provider_fit(days, cfg, model):
    # Explicit lower and upper bounds protect future uses of the shared fitter.
    training = [d for d in days if cfg["calibration_start"] <= d["date"] <= cfg["calibration_end"]]
    expected = len(list(dates_between(cfg["calibration_start"], cfg["calibration_end"])))
    if len(training) != expected or len({d["date"] for d in training}) != expected:
        raise ValueError("Incomplete or duplicated fixed calibration window")
    return calibrate(training, {**cfg, "forecast_models": [model]})


def paired_interval(values, cfg):
    if not values:
        raise ValueError("Empty calendar scope")
    rng, totals = random.Random(cfg["bootstrap_seed"]), []
    for _ in range(cfg["bootstrap_samples"]):
        draw = []
        while len(draw) < len(values):
            start = rng.randrange(len(values))
            draw.extend(values[(start + j) % len(values)] for j in range(3))
        totals.append(sum(draw[:len(values)]))
    totals.sort()
    return [round(totals[int(len(totals) * q)], 6) for q in (.025, .975)]


def forecast_quality(days, fit):
    errors, raw_errors, briers = [], [], []
    for d in days:
        raw_mean = sum(fit["weights"][m] * d["model_maxes"][m] for m in fit["weights"])
        mean = raw_mean + fit["bias_f"]
        errors.append(d["actual_iem_high"] - mean)
        raw_errors.append(d["actual_iem_high"] - raw_mean)
        briers.append(sum((bucket_probability(mean, fit["sigma_f"], m["bounds"]) - m["yes_settlement"]) ** 2 for m in d["markets"]))
    return {"days": len(days), "mae_f": statistics.mean(abs(e) for e in errors), "raw_uncorrected_mae_f": statistics.mean(abs(e) for e in raw_errors),
            "rmse_f": math.sqrt(statistics.mean(e * e for e in errors)), "mean_categorical_brier": statistics.mean(briers)}


def scenarios(cfg):
    yield "primary", 60, .01, 60, 5
    yield "age_5", 5, .01, 60, 5
    yield "age_300", 300, .01, 60, 5
    yield "markup_0.025", 60, .025, 60, 5
    yield "markup_0.05", 60, .05, 60, 5
    yield "latency_1", 60, .01, 1, 5
    yield "latency_300", 60, .01, 300, 5
    yield "shares_10", 60, .01, 60, 10


def evaluate(cfg):
    books = read_json(DATA / "books-manifest.json")
    if not books["complete"] or books["config_sha256"] != digest(CONFIG):
        raise ValueError("Complete matching book acquisition required")
    prior, prior_control = read_json(PRIOR), read_json(PRIOR_CONTROL)
    report = {"config": cfg, "config_sha256": digest(CONFIG), "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "live_execution_verified": False, "profitability_certified": False, "provider_subscription_costs_included": False,
              "city_results": {}, "aggregates": {}, "paired_comparisons": {}}
    all_results = defaultdict(list)
    for p in cfg["profiles"]:
        city = p["city"]
        days, issues = deep.load_city(p, cfg)
        evaluation = [d for d in days if p["evaluation_start"] <= d["date"] <= p["evaluation_end"]]
        if len(evaluation) != len(list(dates_between(p["evaluation_start"], p["evaluation_end"]))):
            raise ValueError("Shortened evaluation window")
        source = [r for meta in books["days"] if meta["city"] == city for r in read_json(ROOT / meta["snapshot_path"])]
        city_results = {"issues": issues, "markets": sum(len(d["markets"]) for d in evaluation), "providers": {}}
        for model in cfg["comparison_models"]:
            if model == "frozen_original":
                fit, use_days = prior["city_results"][city]["fit"], evaluation
            elif model == "persistence":
                fit = prior_control["cities"][city]["fit"]
                highs, history_issues = observed_highs(p, with_coverage=True)
                use_days = persistence_rows(evaluation, highs)
                relevant = {(date.fromisoformat(d["date"]) - timedelta(days=i)).isoformat() for d in evaluation for i in range(1, 8)}
                city_results["control_history_issues"] = [i for i in history_issues if i["date"] in relevant]
            else:
                fit, use_days = provider_fit(days, cfg, model), evaluation
            cases = {}
            for name, age, markup, delay, shares in scenarios(cfg):
                actual = {**cfg, "initial_capital": cfg["initial_capital_per_city"],
                          "decision_utc_hour": cfg["decision_local_hour"] - p["utc_offset_hours"],
                          "execution_latency_seconds": delay, "order_shares": shares}
                snapshots = {(r["date"], r["condition_id"], r["outcome"]): {**r, "signal": r["states"]["0"], "execution": r["states"][str(delay)]} for r in source}
                result = run_scenario(use_days, snapshots, actual, fit, age, markup)
                path = OUTPUT / city / model / (name + ".json")
                write_json(path, result)
                cases[name] = {"summary": result["summary"], "result_path": str(path.relative_to(ROOT)), "sha256": digest(path),
                               "max_age": age, "markup": markup, "latency": delay, "shares": shares}
                all_results[(model, name)].append((city, result))
            city_results["providers"][model] = {"fit": fit, "forecast_quality": forecast_quality(use_days, fit), "scenarios": cases}
        report["city_results"][city] = city_results
    for (model, scenario), results in all_results.items():
        daily, fills, attempts = defaultdict(float), [], []
        for city, result in results:
            fills.extend({**f, "city": city} for f in result["fills"])
            attempts.extend({**f, "city": city} for f in result["attempts"])
            for r in result["daily"]:
                daily[r["date"]] += r["pnl"]
        calendar = [{"date": ds, "pnl": val} for ds, val in sorted(daily.items())]
        summary = summarize(fills, attempts, calendar, {**cfg, "initial_capital": cfg["initial_capital_per_city"] * len(cfg["profiles"])})
        summary["fill_model"] = "conditional distinct-outcome displayed ask/depth; cities clustered by calendar date"
        summary["city_days"] = cfg["expected_new_city_days"]
        summary["pnl_95pct_interval_is_unreliable_with_seven_days"] = True
        report["aggregates"].setdefault(model, {})[scenario] = summary
        write_json(OUTPUT / model / (scenario + "_daily.json"), calendar)
        if scenario == "primary":
            write_json(OUTPUT / model / "primary_fills.json", fills)
            write_csv(OUTPUT / model / "primary_fills.csv", fills)
    for model, reference in (("frozen_original", "persistence"), ("ncep_nbm_conus", "frozen_original"), ("ncep_nbm_conus", "persistence")):
        a = read_json(OUTPUT / model / "primary_daily.json")
        b = read_json(OUTPUT / reference / "primary_daily.json")
        if [r["date"] for r in a] != [r["date"] for r in b]:
            raise ValueError("Paired comparison dates differ")
        paired = [{"date": x["date"], "incremental_pnl": x["pnl"] - y["pnl"]} for x, y in zip(a, b)]
        values = [r["incremental_pnl"] for r in paired]
        report["paired_comparisons"][model + "_minus_" + reference] = {"days": len(values), "net_pnl_difference": round(sum(values), 6), "three_day_block_95pct": paired_interval(values, cfg)}
        write_csv(OUTPUT / (model + "_minus_" + reference + ".csv"), paired)
    files = [CONFIG, Path(__file__), PRIOR, PRIOR_CONTROL, ROOT / "weather_deep_research.py", ROOT / "weather_backtest.py",
             ROOT / "weather_full_book_backtest.py", ROOT / "btc_data.py", ROOT / "weather_book_check.py", ROOT / "expanded_book_data.py", ROOT / "weather_archive_ranges.py"]
    files.extend([CONFIG.parent / "sources.json", CONFIG.parent / "requirements.txt", ROOT / ".probe-deps/setup_manifest.json"])
    files.extend(path for path in DATA.rglob("*") if path.is_file() and path.suffix in (".json", ".parquet") and "progress" not in path.name)
    write_json(OUTPUT / "input_manifest.json", {"files": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)} for p in sorted(set(files))]})
    report["input_manifest_sha256"] = digest(OUTPUT / "input_manifest.json")
    write_json(OUTPUT / "summary.json", report)
    print(json.dumps({m: c["primary"] for m, c in report["aggregates"].items()}, indent=2))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def verify(cfg):
    report = read_json(OUTPUT / "summary.json")
    if report["config_sha256"] != digest(CONFIG) or report["input_manifest_sha256"] != digest(OUTPUT / "input_manifest.json"):
        raise ValueError("Config/input manifest changed")
    for entry in read_json(OUTPUT / "input_manifest.json")["files"]:
        if digest(ROOT / entry["path"]) != entry["sha256"]:
            raise ValueError("Input hash mismatch: " + entry["path"])
    prior, control = read_json(PRIOR), read_json(PRIOR_CONTROL)
    books = read_json(DATA / "books-manifest.json")
    checked = 0
    calendar_totals = defaultdict(lambda: defaultdict(float))
    for p in cfg["profiles"]:
        city = p["city"]
        days, _ = deep.load_city(p, cfg)
        evaluation = [d for d in days if p["evaluation_start"] <= d["date"] <= p["evaluation_end"]]
        markets = {(d["date"], m["condition_id"]): m for d in evaluation for m in d["markets"]}
        raw = {(r["date"], r["condition_id"], r["outcome"]): r for d in books["days"] if d["city"] == city for r in read_json(ROOT / d["snapshot_path"])}
        expected = {(ds, cid, outcome): token for (ds, cid), m in markets.items() for outcome, token in m["tokens"].items()}
        if raw.keys() != expected.keys() or any(raw[k]["token_id"] != token for k, token in expected.items()):
            raise ValueError("Incomplete or misidentified outcome scope")
        for record in raw.values():
            if record["decision_timestamp"] != deep.decision_time(record["date"], cfg, p):
                raise ValueError("Wrong decision timezone")
            for delay, state in record["states"].items():
                if state["timestamp"] != record["decision_timestamp"] + int(delay):
                    raise ValueError("Wrong snapshot boundary")
                if state["book_quote_recv_ts"] is not None and state["book_quote_recv_ts"] >= state["timestamp"]:
                    raise ValueError("Future received event")
        for model, provider in report["city_results"][city]["providers"].items():
            fit = provider["fit"]
            if model == "frozen_original" and fit != prior["city_results"][city]["fit"]:
                raise ValueError("Original fit changed")
            if model == "persistence" and fit != control["cities"][city]["fit"]:
                raise ValueError("Original control fit changed")
            if fit["through"] != cfg["calibration_end"]:
                raise ValueError("Fit includes later data")
            for name, case in provider["scenarios"].items():
                path = ROOT / case["result_path"]
                if digest(path) != case["sha256"]:
                    raise ValueError("Result hash mismatch")
                result = read_json(path)
                if {d["date"] for d in result["daily"]} != {d["date"] for d in evaluation}:
                    raise ValueError("Shortened calendar scope")
                if len({a["date"] for a in result["attempts"]}) != len(result["attempts"]):
                    raise ValueError("Multiple attempts per city-day")
                for attempt in result["attempts"]:
                    eligible = [c for c in result["candidates"] if c["date"] == attempt["date"] and c["signal_reason"] == "eligible"]
                    best = max(eligible, key=lambda c: (c["estimated_net_edge"], c["condition_id"], c["outcome"]))
                    if (best["condition_id"], best["outcome"]) != (attempt["condition_id"], attempt["outcome"]):
                        raise ValueError("Fill affected candidate ranking")
                pnl, fee_total = Decimal(0), Decimal(0)
                for f in result["fills"]:
                    r = raw[(f["date"], f["condition_id"], f["outcome"])]
                    m = markets[(f["date"], f["condition_id"])]
                    for delay in ("0", str(case["latency"])):
                        state = r["states"][delay]
                        if book_reason(state, state["timestamp"], case["max_age"], case["shares"]):
                            raise ValueError("Invalid/stale/insufficient displayed depth")
                    price = Decimal(str(round(r["states"][str(case["latency"])]["book_ask"] + case["markup"], 10)))
                    shares = Decimal(str(case["shares"]))
                    fee = (shares * Decimal(str(cfg["current_taker_fee_rate"])) * price * (1 - price)).quantize(Decimal(".00001"), rounding=ROUND_HALF_UP)
                    cost = shares * price + fee
                    winner = m["yes_settlement"] if f["outcome"] == "Yes" else 1 - m["yes_settlement"]
                    payout = shares * Decimal(str(winner))
                    if f["token_id"] != r["token_id"] or f["shares"] != case["shares"] or f["fill_timestamp"] != r["decision_timestamp"] + case["latency"]:
                        raise ValueError("Wrong token/size/fill time")
                    if f["fill_timestamp"] >= min(m["close_timestamp"], m["resolution_timestamp"]) or float(price) > r["states"]["0"]["book_ask"] + cfg["max_price_move"] + 1e-9:
                        raise ValueError("Late fill or adverse price limit")
                    for key, value in (("fees", fee), ("entry_price", price), ("cost", cost), ("payout", payout), ("pnl", payout - cost)):
                        if abs(f[key] - float(value)) > 1e-7:
                            raise ValueError("Independent accounting mismatch: " + key)
                    day = next(d for d in evaluation if d["date"] == f["date"])
                    if model == "persistence":
                        day = persistence_rows([day], observed_highs(p))[0]
                    mean = sum(fit["weights"][model_name] * day["model_maxes"][model_name] for model_name in fit["weights"]) + fit["bias_f"]
                    yes_probability = bucket_probability(mean, fit["sigma_f"], m["bounds"])
                    probability = yes_probability if f["outcome"] == "Yes" else 1 - yes_probability
                    if abs(mean - f["forecast_mu_f"]) > 1e-9 or abs(probability - f["model_probability"]) > 1e-9:
                        raise ValueError("Wrong day/provider probability")
                    pnl += payout - cost
                    fee_total += fee
                    checked += 1
                if abs(float(pnl) - case["summary"]["net_pnl"]) > 1e-6 or abs(float(fee_total) - case["summary"]["fees"]) > 1e-6:
                    raise ValueError("Summary accounting mismatch")
                for d in result["daily"]:
                    calendar_totals[(model, name)][d["date"]] += d["pnl"]
    for (model, name), days in calendar_totals.items():
        if abs(sum(days.values()) - report["aggregates"][model][name]["net_pnl"]) > 1e-6:
            raise ValueError("Combined calendar accounting mismatch")
    result = {"status": "passed", "checked_at_utc": datetime.now(timezone.utc).isoformat(), "scenario_fills_checked": checked,
              "city_days": cfg["expected_new_city_days"], "markets": sum(c["markets"] for c in report["city_results"].values()),
              "input_files_hashed": len(read_json(OUTPUT / "input_manifest.json")["files"]), "summary_sha256": digest(OUTPUT / "summary.json"),
              "scope": "Data identity, fixed fit, point-in-time books and accounting; not profitability or live fills"}
    write_json(OUTPUT / "verification.json", result)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("collect", "books", "evaluate", "verify"))
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    cfg = configure()
    if args.stage == "collect":
        deep.collect(cfg, max(1, min(args.workers, 4)))
    elif args.stage == "books":
        deep.acquire_books(cfg, max(1, min(args.workers, 2)))
    elif args.stage == "evaluate":
        evaluate(cfg)
    else:
        verify(cfg)


if __name__ == "__main__":
    main()
