"""Fixed-rule weather extension, attribution and capacity research. No live orders."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
import math
from pathlib import Path
import statistics

from btc_data import Book
from expanded_book_data import connect, sha256
from weather_backtest import bucket_probability, calibrate, parse_bucket, summarize, load_days as baseline_load_days, load_observations as baseline_load_observations
from weather_book_check import archive_depth_events
from weather_data import ROOT, dates_between, fetch, read_json, request_url, write_json
from weather_full_book_backtest import cost_for, run_scenario

CONFIG = ROOT / "research/weather_deep/config.json"
DATA = ROOT / "data/weather_deep"
OUTPUT = ROOT / "results/weather_deep"


def day_path(profile, folder, ds):
    # Original Chicago sources are immutable inputs, never rewritten here.
    prior = ROOT / "data/weather" / folder / f"{ds}.json"
    if profile["city"] == "chicago" and "2026-08-01" <= ds <= "2026-09-29" and prior.exists():
        return prior
    return DATA / profile["city"] / folder / f"{ds}.json"


def validate_rules(market, profile):
    description = market.get("description", "")
    source = market.get("resolutionSource", "").lower()
    if profile["station_phrase"] not in description or "whole degrees Fahrenheit" not in description:
        raise ValueError("Station/rounding differs from the fixed profile")
    if profile["station"].lower() not in source or not any(s in source for s in ("weather.gov", "wunderground.com")):
        raise ValueError("Unsupported resolution source")
    # The transition date Aug23 used NOAA without an explicit hourly phrase.
    # Preserve that rule difference instead of dropping a calibration day.
    convention = "hourly" if "Show Hourly Data" in description else "frequency_unspecified"
    return source + "|" + convention


def collect_one(profile, day, cfg):
    ds, city = day.isoformat(), profile["city"]
    slug = f"highest-temperature-in-{city}-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"
    event = fetch(request_url("https://gamma-api.polymarket.com/events", {"slug": slug}), day_path(profile, "events", ds))
    if len(event) != 1 or not event[0].get("markets"):
        raise ValueError("Missing daily event")
    forecast = fetch(request_url("https://single-runs-api.open-meteo.com/v1/forecast", {
        "latitude": profile["latitude"], "longitude": profile["longitude"], "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit", "timezone": profile["timezone"],
        "models": ",".join(cfg["forecast_models"]), "run": ds + "T00:00", "forecast_days": 2,
    }), day_path(profile, "forecasts", ds))
    if forecast["utc_offset_seconds"] != profile["utc_offset_hours"] * 3600:
        raise ValueError("DST/offset differs; do not apply a fixed-hour shortcut")
    for model in cfg["forecast_models"]:
        values = [v for t, v in zip(forecast["hourly"]["time"], forecast["hourly"]["temperature_2m_" + model]) if t.startswith(ds)]
        if len(values) != 24 or any(v is None or not math.isfinite(v) for v in values):
            raise ValueError("Missing historical forecast hours")
    markets = []
    for market in event[0]["markets"]:
        validate_rules(market, profile)
        cid = market["conditionId"]
        resolution_path = day_path(profile, "resolutions", cid)
        # Resolution cache names are condition IDs, not dates; reuse Chicago separately.
        old = ROOT / "data/weather/resolutions" / (cid + ".json")
        if city == "chicago" and old.exists():
            resolution_path = old
        body = fetch(request_url("https://data-api.polymarket.com/v2/resolutions", {"condition": cid}), resolution_path)
        records = [r for r in body["data"] if r.get("condition_id") == cid and r.get("status") == "resolved"]
        if len(records) != 1 or not records[0].get("transaction_hash"):
            raise ValueError("Unresolved/ambiguous market")
        markets.append({"condition_id": cid, "resolution_path": str(resolution_path.relative_to(ROOT))})
    return {"city": city, "date": ds, "markets": markets, "status": "ok"}


def collect(cfg, workers):
    manifest = {"config_sha256": sha256(CONFIG), "requested_new_city_days": cfg["expected_new_city_days"], "days": [], "errors": []}
    jobs = [(p, d) for p in cfg["profiles"] for d in dates_between(cfg["calibration_start"], p["evaluation_end"])
            if d.isoformat() <= cfg["calibration_end"] or d.isoformat() >= p["evaluation_start"]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(collect_one, p, d, cfg): (p["city"], d.isoformat()) for p, d in jobs}
        for future in as_completed(pending):
            try:
                manifest["days"].append(future.result())
            except Exception as exc:
                manifest["errors"].append({"job": pending[future], "error": str(exc)})
            if (len(manifest["days"]) + len(manifest["errors"])) % 10 == 0:
                print(f"metadata {len(manifest['days'])}/{len(jobs)} errors={len(manifest['errors'])}", flush=True)
                write_json(DATA / "metadata-progress.json", manifest)
    for p in cfg["profiles"]:
        try:
            url = request_url("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", {
                "station": p["iem_station"], "data": ["tmpf", "metar"], "sts": cfg["calibration_start"] + "T00:00:00Z",
                "ets": (date.fromisoformat(p["evaluation_end"]) + timedelta(days=2)).isoformat() + "T00:00:00Z",
                "tz": "UTC", "format": "onlycomma", "latlon": "no", "elev": "no", "missing": "M",
                "trace": "T", "direct": "no", "report_type": [3],
            })
            fetch(url, DATA / p["city"] / "observations.json", text=True)
        except Exception as exc:
            manifest["errors"].append({"job": [p["city"], "observations"], "error": str(exc)})
    manifest["days"].sort(key=lambda x: (x["city"], x["date"]))
    manifest["requested_metadata_city_days"] = len(jobs)
    manifest["complete"] = not manifest["errors"] and len(manifest["days"]) == len(jobs)
    manifest["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(DATA / "metadata-manifest.json", manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != "days"}, indent=2), flush=True)
    if not manifest["complete"]:
        raise SystemExit(2)


def load_city(p, cfg):
    manifest = read_json(DATA / "metadata-manifest.json")
    if not manifest["complete"] or manifest["config_sha256"] != sha256(CONFIG):
        raise ValueError("Metadata incomplete or config changed")
    offset = timezone(timedelta(hours=p["utc_offset_hours"]))
    observations = defaultdict(list)
    for row in csv.DictReader(io.StringIO(read_json(DATA / p["city"] / "observations.json")["body"])):
        if row["station"] != p["iem_station"] or not row["metar"].startswith(p["station"] + " ") or row["tmpf"] in ("M", ""):
            continue
        valid = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        value = float(row["tmpf"])
        if not math.isfinite(value) or not -50 < value < 130:
            raise ValueError("Invalid station temperature")
        observations[valid.astimezone(offset).date().isoformat()].append({"timestamp": valid.timestamp(), "fahrenheit": value})
    days, issues = [], []
    for record in (d for d in manifest["days"] if d["city"] == p["city"]):
        ds = record["date"]
        event = read_json(day_path(p, "events", ds))["body"][0]
        forecast = read_json(day_path(p, "forecasts", ds))["body"]
        obs = sorted(observations[ds], key=lambda x: x["timestamp"])
        hours = {datetime.fromtimestamp(o["timestamp"], offset).hour for o in obs}
        if len(hours) != 24:
            issues.append({"date": ds, "observation_hours": len(hours)})
        if not obs:
            raise ValueError("Missing entire station day")
        maxes = {model: max(v for t, v in zip(forecast["hourly"]["time"], forecast["hourly"]["temperature_2m_" + model]) if t.startswith(ds)) for model in cfg["forecast_models"]}
        by_cid = {m["condition_id"]: m for m in record["markets"]}
        markets = []
        for market in event["markets"]:
            cid = market["conditionId"]
            resolution = read_json(ROOT / by_cid[cid]["resolution_path"])["body"]["data"]
            r = next(r for r in resolution if r["condition_id"] == cid and r["status"] == "resolved")
            yes = int(r["price"]) / 1e18
            outcomes, tokens = json.loads(market["outcomes"]), json.loads(market["clobTokenIds"])
            if yes not in (0, 1) or set(outcomes) != {"Yes", "No"} or len(tokens) != 2:
                raise ValueError("Nonbinary settlement/outcome")
            markets.append({"condition_id": cid, "title": market["groupItemTitle"], "bounds": parse_bucket(market["groupItemTitle"]),
                "yes_settlement": yes, "resolution_timestamp": int(r["last_update_timestamp"]),
                "close_timestamp": datetime.fromisoformat(market["closedTime"].replace("Z", "+00:00")).timestamp(),
                "settlement_source": validate_rules(market, p), "resolution_transaction_hash": r["transaction_hash"],
                "tokens": dict(zip(outcomes, tokens))})
        ordered = sorted(markets, key=lambda m: m["bounds"][0])
        if (sum(m["yes_settlement"] for m in markets) != 1 or ordered[0]["bounds"][0] != -math.inf
                or ordered[-1]["bounds"][1] != math.inf
                or any(a["bounds"][1] + 1 != b["bounds"][0] for a, b in zip(ordered, ordered[1:]))):
            raise ValueError("Buckets not exhaustive/exclusive or winner ambiguous")
        actual = math.floor(max(o["fahrenheit"] for o in obs) + .5)
        winner = next(m for m in markets if m["yes_settlement"] == 1)
        if not winner["bounds"][0] <= actual <= winner["bounds"][1]:
            issues.append({"date": ds, "iem_high": actual, "official_winner": winner["title"]})
        days.append({"date": ds, "model_maxes": maxes, "actual_iem_high": actual, "observations": obs, "markets": markets})
    return days, issues


def sample_times(events, token, decision, delays):
    """Apply each actual received event once, strictly before every boundary."""
    book = Book(max_receive_gap_seconds=None, reconcile_after_delta=True, allow_one_sided=True)
    counts, result, index = Counter(), {}, 0
    rows = sorted((e for e in events if e["symbol"] == token), key=lambda e: e["recv_ts"])
    for delay in sorted({0, *delays}):
        at = decision + delay
        while index < len(rows) and rows[index]["recv_ts"] < at:
            book.update(rows[index], counts)
            index += 1
        result[str(delay)] = {"timestamp": at, **book.features("book", at), "initial_snapshot_present": book.snapshot_ts is not None}
    return result, dict(counts)


def decision_time(ds, cfg, profile):
    return int(datetime.fromisoformat(ds).replace(hour=cfg["decision_local_hour"] - profile["utc_offset_hours"], tzinfo=timezone.utc).timestamp())


def subset(con, city, ds, hour, conditions, start, end):
    condition_hash = hashlib.sha256(json.dumps(sorted(conditions)).encode()).hexdigest()[:12]
    stem = f"{ds}T{hour:02d}-{condition_hash}-{start}-{end}"
    folder = DATA / city / "books"
    folder.mkdir(parents=True, exist_ok=True)
    path, meta = folder / (stem + ".parquet"), folder / (stem + ".manifest.json")
    if path.exists() and meta.exists():
        prior = read_json(meta)
        if prior["subset_sha256"] == sha256(path):
            return path, prior
    url = f"https://dl.pendulumflow.com/v3/{ds}/{hour:02d}/{ds}T{hour:02d}.parquet"
    temporary = path.with_suffix(".parquet.part")
    con.execute("""COPY (SELECT event_type,timestamp_received,timestamp,market,asset_id,best_bid,best_ask,bids,asks,new_tick_size,sequence,price,size,side
        FROM read_parquet($source) WHERE market IN (SELECT unnest($conditions))
        AND event_type IN ('book','best_bid_ask','price_change','tick_size_change')
        AND timestamp_received>=to_timestamp($start) AND timestamp_received<to_timestamp($end))
        TO $destination (FORMAT PARQUET)""", {"source": url, "conditions": [bytes.fromhex(c[2:]) for c in conditions],
        "start": start, "end": end, "destination": str(temporary)})
    temporary.replace(path)
    count = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
    record = {"source_url": url, "source_full_hash_verified": False, "source_published_hash": None,
        "source_manifest_status": "API503 at study freeze; direct Parquet used", "condition_ids": sorted(conditions),
        "start": start, "end": end, "rows": count, "subset_sha256": sha256(path), "subset_bytes": path.stat().st_size,
        "subset_path": str(path.relative_to(ROOT))}
    write_json(meta, record)
    return path, record


def acquire_day_books(p, day, cfg):
    delays = sorted({cfg["execution_latency_seconds"], *cfg["sensitivity_latency_seconds"]})
    ds = day["date"]
    snapshot_path = DATA / p["city"] / "books" / (ds + ".snapshots.json")
    day_meta_path = snapshot_path.with_suffix(".manifest.json")
    if snapshot_path.exists() and day_meta_path.exists() and read_json(day_meta_path)["snapshots_sha256"] == sha256(snapshot_path):
        return read_json(day_meta_path)
    con = connect()
    con.execute("SET threads=2")
    con.execute("SET memory_limit='512MB'")
    try:
        decision = decision_time(ds, cfg, p)
        conditions = [m["condition_id"] for m in day["markets"]]
        parts, events = [], []
        for start, end in ((decision - 3600, decision), (decision, decision + cfg["book_end_after_decision_seconds"])):
            at = datetime.fromtimestamp(start, timezone.utc)
            path, meta = subset(con, p["city"], at.date().isoformat(), at.hour, conditions, start, end)
            parts.append(meta)
            events.extend(archive_depth_events(con, path, ds))
        for lookback in range(2, cfg["book_max_lookback_hours"] + 1):
            seeded = {e["symbol"] for e in events if e["event_type"] == "book" and e["recv_ts"] < decision}
            missing = [m["condition_id"] for m in day["markets"] if decision < min(m["close_timestamp"], m["resolution_timestamp"])
                       and not set(m["tokens"].values()).issubset(seeded)]
            if not missing:
                break
            start = decision - lookback * 3600
            at = datetime.fromtimestamp(start, timezone.utc)
            path, meta = subset(con, p["city"], at.date().isoformat(), at.hour, missing, start, start + 3600)
            parts.append(meta)
            events.extend(archive_depth_events(con, path, ds))
        by_token = defaultdict(list)
        for e in events:
            by_token[e["symbol"]].append(e)
        snapshots = []
        for m in day["markets"]:
            for outcome, token in m["tokens"].items():
                states, counts = sample_times(by_token[token], token, decision, delays)
                snapshots.append({"city": p["city"], "date": ds, "condition_id": m["condition_id"], "outcome": outcome,
                    "token_id": token, "decision_timestamp": decision, "states": states, "reconstruction_counts": counts})
        write_json(snapshot_path, snapshots)
        meta = {"city": p["city"], "date": ds, "snapshot_path": str(snapshot_path.relative_to(ROOT)),
            "snapshots_sha256": sha256(snapshot_path), "outcomes": len(snapshots), "parts": parts,
            "missing_seed": sum(not r["states"]["0"]["initial_snapshot_present"] for r in snapshots)}
        write_json(day_meta_path, meta)
        return meta
    finally:
        con.close()


def acquire_books(cfg, workers):
    records, errors, jobs = [], [], []
    for p in cfg["profiles"]:
        days, _ = load_city(p, cfg)
        jobs.extend((p, d) for d in days if d["date"] >= p["evaluation_start"])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(acquire_day_books, p, d, cfg): (p["city"], d["date"]) for p, d in jobs}
        for future in as_completed(futures):
            city, ds = futures[future]
            try:
                meta = future.result()
                records.append(meta)
                print(city, ds, "rows", sum(x["rows"] for x in meta["parts"]), "missing_seed", meta["missing_seed"], flush=True)
            except Exception as exc:
                errors.append({"city": city, "date": ds, "error": str(exc)})
                print(city, ds, "ERROR", str(exc)[:160], flush=True)
            write_json(DATA / "books-progress.json", {"requested_city_days": cfg["expected_new_city_days"], "completed_city_days": len(records), "errors": errors})
    records.sort(key=lambda d: (d["city"], d["date"]))
    manifest = {"config_sha256": sha256(CONFIG), "requested_city_days": cfg["expected_new_city_days"],
        "complete": len(records) == cfg["expected_new_city_days"] and not errors, "days": records, "errors": errors,
        "missing_initial_snapshots": sum(r["missing_seed"] for r in records), "created_at_utc": datetime.now(timezone.utc).isoformat()}
    write_json(DATA / "books-manifest.json", manifest)
    if not manifest["complete"]:
        raise SystemExit(2)


def scenario_specs(cfg):
    yield "primary", {}
    for key, values in (("age", cfg["sensitivity_max_quote_age_seconds"]), ("latency", cfg["sensitivity_latency_seconds"]),
                        ("markup", cfg["stress_price_markups"]), ("shares", cfg["sensitivity_order_shares"]), ("edge", cfg["sensitivity_net_edges"])):
        for v in values:
            yield f"{key}_{v}", {key: v}


def probability_diagnostics(days, fit, cfg):
    models = {"weighted": fit}
    train = [d for d in days if d["date"] <= cfg["calibration_end"]]
    for model in (*cfg["forecast_models"], "equal"):
        weights = {m: (1 / len(cfg["forecast_models"]) if model == "equal" else float(m == model)) for m in cfg["forecast_models"]}
        residuals = [d["actual_iem_high"] - sum(weights[m] * d["model_maxes"][m] for m in weights) for d in train]
        models[model] = {"weights": weights, "bias_f": statistics.mean(residuals), "sigma_f": max(cfg["sigma_floor_fahrenheit"], statistics.stdev(residuals))}
    metrics = {}
    for model, f in models.items():
        rows = []
        for d in days:
            if d["date"] <= cfg["calibration_end"]:
                continue
            mu = sum(f["weights"][m] * d["model_maxes"][m] for m in f["weights"]) + f["bias_f"]
            probabilities = [bucket_probability(mu, f["sigma_f"], m["bounds"]) for m in d["markets"]]
            if abs(sum(probabilities) - 1) > 1e-8:
                raise ValueError("Forecast bucket probabilities do not sum to one")
            win = next(q for q, m in zip(probabilities, d["markets"]) if m["yes_settlement"] == 1)
            rows.append({"date": d["date"], "mu": mu, "actual_iem_high": d["actual_iem_high"], "error": d["actual_iem_high"] - mu,
                "categorical_brier": sum((q - m["yes_settlement"]) ** 2 for q, m in zip(probabilities, d["markets"])),
                "winner_probability": win, "log_loss": -math.log(max(1e-12, win)),
                "within_95pct_interval": abs(d["actual_iem_high"] - mu) <= 1.96 * f["sigma_f"]})
        metrics[model] = {"fit": f, "days": len(rows), "mae_f": statistics.mean(abs(r["error"]) for r in rows),
            "bias_actual_minus_forecast_f": statistics.mean(r["error"] for r in rows),
            "rmse_f": math.sqrt(statistics.mean(r["error"] ** 2 for r in rows)),
            "mean_categorical_brier": statistics.mean(r["categorical_brier"] for r in rows),
            "mean_log_loss": statistics.mean(r["log_loss"] for r in rows),
            "95pct_temperature_interval_coverage": statistics.mean(r["within_95pct_interval"] for r in rows), "daily": rows}
    return metrics


def attribution(result, snapshots):
    fills = result["fills"]
    groupings = {}
    for label, classify in (("outcome", lambda f: f["outcome"]), ("week", lambda f: f["date"][:7] + "-w" + str((int(f["date"][-2:]) - 1) // 7 + 1)),
                            ("entry_price_band", lambda f: "0-.2" if f["entry_price"] < .2 else ".2-.5" if f["entry_price"] < .5 else ".5-.8" if f["entry_price"] < .8 else ".8-1")):
        groups = defaultdict(list)
        for f in fills:
            groups[classify(f)].append(f)
        groupings[label] = {k: {"fills": len(v), "net_pnl": round(sum(f["pnl"] for f in v), 6), "wins": sum(f["settlement"] == 1 for f in v)} for k, v in groups.items()}
    ordered = sorted(result["daily"], key=lambda d: d["pnl"], reverse=True)
    total = sum(d["pnl"] for d in ordered)
    stress = {}
    for markup in (.025, .05):
        cfg = {"order_shares": fills[0]["shares"] if fills else 5, "current_taker_fee_rate": .05}
        stress[str(markup)] = round(sum(f["payout"] - cost_for(round(min(1.0, f["entry_price"] + markup - f["adverse_price_markup"]), 10), cfg)[0] for f in fills), 6)
    reliability = defaultdict(list)
    forecast_squared, market_squared = [], []
    for f in fills:
        reliability[min(9, int(f["model_probability"] * 10))].append(f)
        row = snapshots[(f["date"], f["condition_id"], f["outcome"])]["signal"]
        if row.get("book_bid") is not None:
            mid = (row["book_bid"] + row["book_ask"]) / 2
            forecast_squared.append((f["model_probability"] - f["settlement"]) ** 2)
            market_squared.append((mid - f["settlement"]) ** 2)
    return {"groupings": groupings, "positive_days": sum(d["pnl"] > 0 for d in ordered), "negative_days": sum(d["pnl"] < 0 for d in ordered),
        "net_pnl_without_largest_winning_day": round(total - sum(d["pnl"] for d in ordered[:1]), 6),
        "net_pnl_without_top_three_days": round(total - sum(d["pnl"] for d in ordered[:3]), 6),
        "leave_one_day_out_pnl_range": [round(total - max(d["pnl"] for d in ordered), 6), round(total - min(d["pnl"] for d in ordered), 6)],
        "daily_ranked": ordered, "fixed_fills_cost_stress": stress,
        "selected_sample_reliability": {str(k / 10): {"n": len(v), "mean_forecast_probability": statistics.mean(f["model_probability"] for f in v),
            "observed_win_fraction": statistics.mean(f["settlement"] for f in v)} for k, v in sorted(reliability.items())},
        "selected_trade_brier_matched_midquotes": {"n": len(market_squared),
            "model": statistics.mean(forecast_squared) if forecast_squared else None, "raw_market_mid": statistics.mean(market_squared) if market_squared else None,
            "scope": "Matched selected binary trades; model-selected sample, not unconditional probability calibration"}}


def prior_research(cfg):
    old = read_json(ROOT / "results/weather_full_books/age_60_markup_0.01.json")
    old_rows = [json.loads(line) for line in (ROOT / "data/weather_full_books/snapshots.jsonl").read_text().splitlines()]
    result = attribution(old, {(r["date"], r["condition_id"], r["outcome"]): r for r in old_rows})
    ages = []
    for f in old["fills"]:
        age = max(f["decision_timestamp"] - f["signal_quote_recv_ts"], f["decision_timestamp"] - f["signal_quote_source_ts"],
                  f["fill_timestamp"] - f["execution_quote_recv_ts"], f["fill_timestamp"] - f["execution_quote_source_ts"])
        ages.append({"date": f["date"], "age": age, "pnl": f["pnl"]})
    result["fixed_baseline_fills_by_age"] = {str(hi): {"fills": sum(lo < r["age"] <= hi for r in ages),
        "pnl": round(sum(r["pnl"] for r in ages if lo < r["age"] <= hi), 6)} for lo, hi in ((0, 5), (5, 15), (15, 30), (30, 60))}
    result["mean_selected_probability"] = statistics.mean(f["model_probability"] for f in old["fills"])
    result["actual_selected_win_fraction"] = statistics.mean(f["settlement"] for f in old["fills"])
    old_cfg = read_json(ROOT / "research/weather_config.json")
    days, issues = baseline_load_days(old_cfg, baseline_load_observations(old_cfg))
    result["probability_diagnostics"] = probability_diagnostics(days, calibrate(days, old_cfg), cfg)
    result["source_quality"] = issues
    return result


def evaluate(cfg):
    manifest = read_json(DATA / "books-manifest.json")
    if not manifest["complete"] or manifest["config_sha256"] != sha256(CONFIG):
        raise ValueError("New book acquisition incomplete or config mismatch")
    report = {"config": cfg, "config_sha256": sha256(CONFIG), "city_results": {}, "prior_chicago": {}, "live_execution_verified": False,
              "primary_new_city_days": cfg["expected_new_city_days"], "sensitivity_type": "exploratory one-factor-at-a-time, no winning setting selected"}
    all_daily, all_fills, all_attempts = defaultdict(float), [], []
    for p in cfg["profiles"]:
        days, issues = load_city(p, cfg)
        fit = calibrate(days, cfg)
        evaluation = [d for d in days if d["date"] >= p["evaluation_start"]]
        source = [r for meta in manifest["days"] if meta["city"] == p["city"] for r in read_json(ROOT / meta["snapshot_path"])]
        cases, primary_snapshots = {}, None
        for name, overrides in scenario_specs(cfg):
            actual = {**cfg, "initial_capital": cfg["initial_capital_per_city"], "decision_utc_hour": cfg["decision_local_hour"] - p["utc_offset_hours"],
                      "order_shares": overrides.get("shares", cfg["order_shares"]), "execution_latency_seconds": overrides.get("latency", cfg["execution_latency_seconds"]),
                      "forecast_min_net_edge": overrides.get("edge", cfg["forecast_min_net_edge"])}
            delay = str(actual["execution_latency_seconds"])
            snapshots = {(r["date"], r["condition_id"], r["outcome"]): {**r, "signal": r["states"]["0"], "execution": r["states"][delay]} for r in source}
            result = run_scenario(evaluation, snapshots, actual, fit, overrides.get("age", cfg["primary_max_quote_age_seconds"]), overrides.get("markup", cfg["base_price_markup"]))
            path = OUTPUT / p["city"] / (name + ".json")
            write_json(path, result)
            cases[name] = {"overrides": overrides, "summary": result["summary"], "result_path": str(path.relative_to(ROOT)), "result_sha256": sha256(path)}
            if name == "primary":
                primary_snapshots = snapshots
                for r in result["daily"]:
                    all_daily[r["date"]] += r["pnl"]
                all_fills.extend({**f, "city": p["city"]} for f in result["fills"])
                all_attempts.extend({**a, "city": p["city"]} for a in result["attempts"])
        primary = read_json(ROOT / cases["primary"]["result_path"])
        report["city_results"][p["city"]] = {"fit": fit, "issues": issues, "evaluation_days": len(evaluation), "scenarios": cases,
            "attribution": attribution(primary, primary_snapshots), "probability_diagnostics": probability_diagnostics(days, fit, cfg)}
    total_daily = [{"date": ds, "pnl": value} for ds, value in sorted(all_daily.items())]
    report["aggregate_primary"] = summarize(all_fills, all_attempts, total_daily, {**cfg, "initial_capital": cfg["initial_capital_per_city"] * len(cfg["profiles"])})
    report["aggregate_primary"]["fill_model"] = "Conditional displayed depth; bootstrap groups all cities by calendar date"
    report["aggregate_primary"]["city_days"] = sum(r["evaluation_days"] for r in report["city_results"].values())
    write_json(OUTPUT / "primary_fills.json", all_fills)
    write_json(OUTPUT / "primary_daily.json", total_daily)
    # Decomposition of the original inspected Chicago period is kept separate.
    report["prior_chicago"] = prior_research(cfg)
    report["aggregate_same_fills_cost_stress"] = {str(markup): round(sum(v["attribution"]["fixed_fills_cost_stress"][str(markup)] for v in report["city_results"].values()), 6) for markup in (.025, .05)}
    inputs = [CONFIG, Path(__file__), ROOT / "weather_full_book_backtest.py", ROOT / "weather_backtest.py", ROOT / "btc_data.py",
              DATA / "metadata-manifest.json", DATA / "books-manifest.json", ROOT / "results/weather_full_books/age_60_markup_0.01.json", ROOT / "data/weather_full_books/snapshots.jsonl"]
    inputs.extend(path for path in DATA.rglob("*.json") if path.name != "metadata-progress.json" and path.name != "books-progress.json" and "probes" not in path.parts)
    for p in cfg["profiles"]:
        for ds in dates_between(cfg["calibration_start"], cfg["calibration_end"]):
            for folder in ("events", "forecasts"):
                inputs.append(day_path(p, folder, ds.isoformat()))
    for d in read_json(DATA / "metadata-manifest.json")["days"]:
        inputs.extend(ROOT / m["resolution_path"] for m in d["markets"])
    inputs.extend([ROOT / "research/weather_config.json", ROOT / "data/weather/observations_hourly.json", ROOT / "data/weather/manifest.json"])
    for folder in ("events", "forecasts", "resolutions"):
        inputs.extend((ROOT / "data/weather" / folder).glob("*.json"))
    write_json(OUTPUT / "input_manifest.json", {"files": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in sorted(set(inputs))]})
    report["input_manifest_sha256"] = sha256(OUTPUT / "input_manifest.json")
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(OUTPUT / "summary.json", report)
    print(json.dumps({"aggregate": report["aggregate_primary"], "cities": {c: v["scenarios"]["primary"]["summary"] for c, v in report["city_results"].items()}}, indent=2))


def verify(cfg):
    report = read_json(OUTPUT / "summary.json")
    if report["config_sha256"] != sha256(CONFIG) or report["input_manifest_sha256"] != sha256(OUTPUT / "input_manifest.json"):
        raise ValueError("Config/input manifest hash changed")
    for record in read_json(OUTPUT / "input_manifest.json")["files"]:
        if sha256(ROOT / record["path"]) != record["sha256"]:
            raise ValueError("Input changed: " + record["path"])
    books = read_json(DATA / "books-manifest.json")
    for d in books["days"]:
        for part in d["parts"]:
            if sha256(ROOT / part["subset_path"]) != part["subset_sha256"]:
                raise ValueError("Archive subset changed")
    checked = 0
    joint_daily = defaultdict(float)
    for p in cfg["profiles"]:
        days, _ = load_city(p, cfg)
        markets = {(d["date"], m["condition_id"]): m for d in days for m in d["markets"]}
        raw = {(r["date"], r["condition_id"], r["outcome"]): r for d in books["days"] if d["city"] == p["city"] for r in read_json(ROOT / d["snapshot_path"])}
        expected = {(d["date"], m["condition_id"], outcome): token for d in days if d["date"] >= p["evaluation_start"] for m in d["markets"] for outcome, token in m["tokens"].items()}
        if raw.keys() != expected.keys() or any(raw[key]["token_id"] != token for key, token in expected.items()):
            raise ValueError("Incomplete/misidentified outcome scope")
        for r in raw.values():
            if r["decision_timestamp"] != decision_time(r["date"], cfg, p):
                raise ValueError("Wrong city decision timezone")
            for delay, state in r["states"].items():
                if state["timestamp"] != r["decision_timestamp"] + int(delay) or (state["book_quote_recv_ts"] is not None and state["book_quote_recv_ts"] >= state["timestamp"]):
                    raise ValueError("Snapshot boundary includes a future event")
        for name, case in report["city_results"][p["city"]]["scenarios"].items():
            if sha256(ROOT / case["result_path"]) != case["result_sha256"]:
                raise ValueError("Result hash mismatch")
            result = read_json(ROOT / case["result_path"])
            overrides = case["overrides"]
            age, latency, markup = overrides.get("age", 60), overrides.get("latency", 60), overrides.get("markup", .01)
            if len({a["date"] for a in result["attempts"]}) != len(result["attempts"]):
                raise ValueError("Multiple attempts per city-day")
            for attempt in result["attempts"]:
                eligible = [c for c in result["candidates"] if c["date"] == attempt["date"] and c["signal_reason"] == "eligible"]
                best = max(eligible, key=lambda c: (c["estimated_net_edge"], c["condition_id"], c["outcome"]))
                if (best["condition_id"], best["outcome"]) != (attempt["condition_id"], attempt["outcome"]):
                    raise ValueError("Fill result affected candidate selection")
            for f in result["fills"]:
                r = raw[(f["date"], f["condition_id"], f["outcome"])]
                m = markets[(f["date"], f["condition_id"])]
                if r["token_id"] != m["tokens"][f["outcome"]] or f["token_id"] != r["token_id"]:
                    raise ValueError("Wrong actual outcome token")
                for delay in (0, latency):
                    book = r["states"][str(delay)]
                    if not book["book_book_valid"] or not book["book_ask_size"] >= f["shares"]:
                        raise ValueError("Invalid/insufficient actual depth")
                    if not 0 < book["timestamp"] - book["book_quote_recv_ts"] <= age or not 0 <= book["timestamp"] - book["book_quote_source_ts"] <= age:
                        raise ValueError("Future/stale input")
                price = r["states"][str(latency)]["book_ask"] + markup
                exact_price = Decimal(str(round(price, 10)))
                fee = float((Decimal(str(f["shares"])) * Decimal(str(cfg["current_taker_fee_rate"])) * exact_price * (1 - exact_price)).quantize(Decimal(".00001"), rounding=ROUND_HALF_UP))
                cost = float(Decimal(str(f["shares"])) * exact_price) + fee
                settlement = m["yes_settlement"] if f["outcome"] == "Yes" else 1 - m["yes_settlement"]
                if f["fill_timestamp"] != f["decision_timestamp"] + latency or f["fill_timestamp"] >= min(m["close_timestamp"], m["resolution_timestamp"]):
                    raise ValueError("Fill timing failure")
                if max(abs(cost - f["cost"]), abs(fee - f["fees"]), abs(settlement * f["shares"] - f["payout"]), abs(f["payout"] - cost - f["pnl"])) > 1e-7:
                    raise ValueError("Fee/payout/PnL mismatch")
                if f["shares"] != overrides.get("shares", cfg["order_shares"]) or price > r["states"]["0"]["book_ask"] + cfg["max_price_move"] + 1e-9:
                    raise ValueError("Changed size or price-cap violation")
                checked += 1
            if abs(sum(f["pnl"] for f in result["fills"]) - case["summary"]["net_pnl"]) > 1e-5:
                raise ValueError("Summary PnL mismatch")
            ledger = [(f["fill_timestamp"], 1, -f["cost"]) for f in result["fills"]] + [(f["resolution_timestamp"], 0, f["payout"]) for f in result["fills"]]
            cash, minimum = cfg["initial_capital_per_city"], cfg["initial_capital_per_city"]
            for _, _, value in sorted(ledger):
                cash += value
                minimum = min(minimum, cash)
                if cash < -1e-8:
                    raise ValueError("Cash spent before settlement")
            if max(abs(cash - case["summary"]["ending_cash"]), abs(minimum - case["summary"]["minimum_cash"])) > 1e-5:
                raise ValueError("Independent cash ledger differs")
            for d in result["daily"]:
                actual_pnl = sum(f["pnl"] for f in result["fills"] if f["date"] == d["date"])
                if abs(d["pnl"] - actual_pnl) > 1e-7:
                    raise ValueError("Daily PnL assigned to wrong date")
                if name == "primary":
                    joint_daily[d["date"]] += actual_pnl
    if report["aggregate_primary"]["city_days"] != cfg["expected_new_city_days"]:
        raise ValueError("Incomplete evaluation scope")
    if {d["date"]: d["pnl"] for d in read_json(OUTPUT / "primary_daily.json")} != dict(sorted(joint_daily.items())):
        raise ValueError("Cities not grouped by actual calendar date")
    write_json(OUTPUT / "validation.json", {"passed": True, "scenario_fills_checked": checked,
        "new_city_days": cfg["expected_new_city_days"], "summary_sha256": sha256(OUTPUT / "summary.json"), "financial_success_certified": False})
    print("Verified", checked, "scenario fills and", cfg["expected_new_city_days"], "new city-days")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "books", "evaluate", "verify"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    cfg = read_json(CONFIG)
    if args.command == "collect":
        collect(cfg, max(1, min(8, args.workers)))
    elif args.command == "books":
        acquire_books(cfg, max(1, min(3, args.workers)))
    elif args.command == "evaluate":
        evaluate(cfg)
    else:
        verify(cfg)


if __name__ == "__main__":
    main()
