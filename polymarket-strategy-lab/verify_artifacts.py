"""Verify saved evidence, independently checking hashes, counts and money math.

Exit 0 means consistent artifacts, never profitable strategies. --allow-pending
also permits absent/stale outputs while work runs, but JSON passed stays false.
No network calls, upstream code imports, order execution, or artifact rewrites.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "evan-kolberg/prediction-market-backtesting": "c76e77af00ef53472a9da8f66dae7fdd2d3e5928",
    "alsk1992/CloddsBot": "c930628c47a4a745f497e29035a3b64622f97cc7",
    "ent0n29/polybot": "51e5362f026adf54162fc1256f453241d93680e6",
    "yangyuan-zhen/PolyWeather": "43e658bbf6286e3cccdf4f09ce478ec90b3fb960",
}
BTC_STRATEGIES = {"complete_set", "btc_fair_value", "late_favorite"}
WEATHER_STRATEGIES = {"forecast_probability_edge", "observed_dead_bucket_no"}


class Pending(Exception):
    pass


def require(value, message):
    if not value:
        raise ValueError(message)


def close(actual, expected, label, tolerance=2e-6):
    require(math.isfinite(float(actual)) and abs(float(actual) - float(expected)) <= tolerance,
            f"{label}: actual={actual}, expected={expected}")


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read(path):
    def reject(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject)


def fresh(result_path, dependencies):
    changed = [str(p.relative_to(ROOT)) for p in dependencies if p.stat().st_mtime_ns > result_path.stat().st_mtime_ns]
    if changed:
        raise Pending("Result predates changed input/code: " + ", ".join(changed[:5]))


def fee(shares, price, rate):
    quantity, probability, coefficient = map(lambda v: Decimal(str(v)), (shares, price, rate))
    return float((quantity * coefficient * probability * (1 - probability)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))


def sources():
    base = ROOT / "references"
    manifest = read(base / "source_manifest.json")
    repos = manifest["repositories"]
    require({r["repository"]: r["commit"] for r in repos} == PINS, "Reference commit pins differ")
    count = 0
    for repo in repos:
        source_root = (base / repo["local_source"]).resolve()
        require(source_root.is_relative_to(base.resolve()), "Source path outside references")
        for entry in repo["files"]:
            path = (source_root / entry["path"]).resolve()
            require(path.is_relative_to(source_root), "Manifest source path traversal")
            require(path.stat().st_size == entry["bytes"] and digest(path) == entry["sha256"], f"Source hash mismatch: {entry['path']}")
            count += 1
        archive = base / "archives" / f"{repo['repository'].split('/')[1]}-{repo['commit']}.zip"
        if archive.exists():
            require(digest(archive) == repo["archive_sha256"], f"Archive hash mismatch: {archive.name}")
    return {"repositories": len(repos), "source_files_hashed": count, "manifest_sha256": digest(base / "source_manifest.json")}


def check_btc_summary(summary, trades, initial):
    require(summary["trades"] == len(trades) and summary["markets"] == len({t["slug"] for t in trades}), "BTC trade/market summary count mismatch")
    require(summary["wins"] == sum(t["pnl"] > 0 for t in trades), "BTC win count mismatch")
    require(summary["partial_pairs"] == sum(bool(t["partial_pair"]) for t in trades), "BTC partial pair count mismatch")
    for field, source in (("cost", "cost"), ("net_pnl", "pnl"), ("fees", "fees")):
        close(summary[field], sum(t[source] for t in trades), "BTC summary " + field)
    close(summary["return_on_initial_cash"], sum(t["pnl"] for t in trades) / initial, "BTC capital return")
    total_cost = sum(t["cost"] for t in trades)
    if total_cost:
        close(summary["roi_on_deployed_cost"], sum(t["pnl"] for t in trades) / total_cost, "BTC cost ROI")
    else:
        require(summary["roi_on_deployed_cost"] is None, "BTC zero-trade ROI must be null")


def btc(expanded=False):
    name = "btc_expanded" if expanded else "btc"
    data = ROOT / "data" / (name + "_replay.csv")
    summary_path = ROOT / "results" / name / "summary.json"
    report = read(summary_path)
    manifest = read(data.with_suffix(".manifest.json"))
    config_path = ROOT / "btc_config.json"
    config = read(config_path)
    expected_config = dict(config, reference_source="binance", oracle_mode="spot_twap_proxy", max_book_snapshot_age_seconds=1) if expanded else config
    require(report["config"] == expected_config, "BTC effective configuration differs from current requested mode")
    if report["config_sha256"] != digest(config_path) or report["data_sha256"] != digest(data):
        raise Pending(f"{name} result input/config hash is stale")
    require(manifest["output_sha256"] == digest(data), f"{name} data manifest hash mismatch")
    fresh(summary_path, [config_path, data, ROOT / "btc_backtest.py"])
    if expanded:
        if not (manifest["status"] == "extracted" and manifest["requested_hours"] == manifest["extracted_hours"] == 30 and not manifest["failures"]):
            raise Pending("Expanded BTC collection has not completed all 30 requested daily hours")
        require(manifest["start_date"] == "2026-09-01" and manifest["end_date"] == "2026-09-30" and manifest["market_count"] == 360, "Expanded BTC scope differs from September 30-day/360-market contract")
        for hour in manifest["hours"]:
            base = ROOT / "data/expanded_books" / hour["hour"]
            for suffix, key in ((".csv", "csv_sha256"), (".parquet", "subset_sha256"), (".source.json", "source_manifest_sha256")):
                require(digest(Path(str(base) + suffix)) == hour[key], f"Expanded hour hash mismatch: {hour['hour']}{suffix}")
    timestamps, slugs, tokens = set(), set(), {}
    rows = 0
    with data.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            ts = float(row["decision_ts"])
            require(math.isfinite(ts) and ts not in timestamps, "Duplicate/nonfinite BTC decision timestamp")
            timestamps.add(ts)
            slugs.add(row["slug"])
            tokens.setdefault(row["slug"], set()).update(str(row[f"{side}_token_id"]) for side in ("up", "down"))
    require(rows == report["rows"] == manifest["counts"]["feature_rows"] and len(slugs) == report["markets"], "BTC input/report counts differ")
    require(len({datetime.fromtimestamp(t, timezone.utc).date() for t in timestamps}) == report["days"], "BTC day count mismatch")
    require(set(report["strategies"]) == BTC_STRATEGIES, "BTC strategy set mismatch")
    close(report["split_ts"], (min(timestamps) + max(timestamps)) / 2, "BTC split")
    for key, value in (("start_utc", min(timestamps)), ("end_utc", max(timestamps))):
        close(datetime.fromisoformat(report[key]).timestamp(), value, "BTC " + key)
    winners = {}
    for slug in slugs:
        settled = read(ROOT / "data/btc_settlements" / (slug + ".json"))
        entries = settled["clob"]["tokens"]
        require(settled["clob"]["closed"] and len(entries) == 2 and sum(bool(t.get("winner")) for t in entries) == 1, "Invalid BTC settlement")
        require({str(t["token_id"]) for t in entries} == tokens[slug], "BTC settlement token mapping mismatch")
        winners[slug] = str(next(t for t in entries if t.get("winner"))["token_id"])
    require(len(winners) == report["settled_markets"], "BTC settlement count mismatch")
    count = 0
    for strategy, result in report["strategies"].items():
        for scenario, delay in (("base", 1), ("stress", 2)):
            path = summary_path.parent / f"{strategy}_{scenario}_trades.json"
            trades = read(path)
            require(len({t["slug"] for t in trades}) == len(trades), "Repeated BTC entry in one market")
            for trade in trades:
                require(trade["strategy"] == strategy and trade["slug"] in winners, "BTC trade identity mismatch")
                require(trade["signal_ts"] in timestamps and trade["cash_release_ts"] >= trade["fill_ts"], "BTC trade time outside data/settlement")
                for leg in trade["legs"]:
                    require(leg["token"] in tokens[trade["slug"]] and 0 < leg["price"] <= 1 and leg["shares"] >= config["min_shares"], "Invalid BTC leg")
                    require(leg["fill_ts"] >= trade["signal_ts"] + delay, "BTC execution precedes configured latency")
                    close(leg["fee"], fee(leg["shares"], leg["price"], config["fee_rate_current"]), "BTC leg fee", 1e-8)
                cost = sum(l["price"] * l["shares"] + l["fee"] for l in trade["legs"])
                payout = sum(l["shares"] for l in trade["legs"] if l["token"] == winners[trade["slug"]])
                for key, value in (("cost", cost), ("payout", payout), ("pnl", payout - cost), ("fees", sum(l["fee"] for l in trade["legs"]))):
                    close(trade[key], value, "BTC trade " + key)
                require(cost <= config["max_cost_per_market"] + 1e-6, "BTC order cost cap exceeded")
            for part, subset in (("all", trades), ("first_half", [t for t in trades if t["signal_ts"] < report["split_ts"]]), ("second_half", [t for t in trades if t["signal_ts"] >= report["split_ts"]])):
                check_btc_summary(result[scenario][part], subset, config["initial_cash"])
            count += len(trades)
    return {"rows": rows, "markets": len(slugs), "trades_checked_across_scenarios": count,
            "data_sha256": report["data_sha256"], "summary_sha256": digest(summary_path),
            "strategy_verdicts": {s: r["status"] for s, r in report["strategies"].items()}}


def weather():
    data = ROOT / "data/weather"
    output = ROOT / "research/weather_results"
    config_path = ROOT / "research/weather_config.json"
    cfg, manifest, report = read(config_path), read(data / "manifest.json"), read(output / "report.json")
    config_hash = digest(config_path)
    if report["config_sha256"] != config_hash or manifest["config_sha256"] != config_hash:
        raise Pending("Weather configuration hash is stale")
    require(report["config"] == cfg and manifest["complete"] and not manifest["errors"], "Weather manifest/config incomplete")
    require(manifest["requested_days"] == len(manifest["days"]) == report["measured_days"] == 60 and len(manifest["markets"]) == 660, "Weather 60-day/660-market scope not met")
    require(all(d["status"] == "ok" for d in manifest["days"]), "Missing weather day")
    require(set(report["strategies"]) == WEATHER_STRATEGIES and report["live_execution_verified"] is False, "Weather scope or execution claim changed")
    raw_count, settlements = 0, {}
    for market in manifest["markets"]:
        cid = market["condition_id"]
        require(market["status"] == "ok" and market["pagination_complete"] and market["resolution_rows"] == 1, "Incomplete weather market")
        paths = sorted((data / "trades" / cid).glob("*.json"))
        require(len(paths) == market["pages"], "Weather page count mismatch")
        count = 0
        for index, path in enumerate(paths):
            page = read(path)["body"]
            require(bool(page["pagination"]["has_more"]) == (index < len(paths) - 1), "Weather pagination terminal flag mismatch")
            require(all(t["condition_id"] == cid for t in page["data"]), "Weather trade market mismatch")
            count += len(page["data"])
        require(count == market["trades"], "Weather trade count differs from manifest")
        raw_count += count
        records = [r for r in read(data / "resolutions" / f"{cid}.json")["body"]["data"] if r["condition_id"] == cid and r["status"] == "resolved"]
        require(len(records) == 1 and bool(records[0]["transaction_hash"]), "Weather chain resolution absent")
        settlements[cid] = records[0]
    require(raw_count == report["integrity"]["raw_trade_rows"] == sum(m["trades"] for m in manifest["markets"]), "Weather raw row summary mismatch")
    fresh(output / "report.json", [ROOT / "weather_backtest.py", config_path, *(p for p in data.rglob("*.json"))])
    fills, attempts = read(output / "fills.json"), read(output / "attempts.json")
    require(len({(f["strategy"], f["date"]) for f in fills}) == len(fills), "Repeated weather event entry")
    for fill in fills:
        record = settlements[fill["condition_id"]]
        yes = int(record["price"]) / 1e18
        payout = yes if fill["outcome"] == "Yes" else 1 - yes
        require(fill["signal_timestamp"] < fill["decision_timestamp"] < fill["fill_timestamp"] < min(fill["close_timestamp"], fill["resolution_timestamp"]), "Weather chronology failure")
        require(fill["fill_timestamp"] >= fill["decision_timestamp"] + cfg["execution_latency_seconds"], "Weather execution latency failure")
        close(fill["settlement"], payout, "Weather settlement")
        require(record["transaction_hash"] == fill["resolution_transaction_hash"], "Weather resolution transaction mismatch")
        close(fill["fees"], fee(fill["shares"], fill["entry_price"], cfg["current_taker_fee_rate"]), "Weather fee", 1e-8)
        close(fill["cost"], fill["shares"] * fill["entry_price"] + fill["fees"], "Weather cost")
        close(fill["pnl"], fill["shares"] * payout - fill["cost"], "Weather PnL")
    for strategy in WEATHER_STRATEGIES:
        for period in ("calibration_in_sample", "out_of_sample"):
            selected = [f for f in fills if f["strategy"] == strategy and f["period"] == period]
            requested = [a for a in attempts if a["strategy"] == strategy and a["period"] == period]
            summary = report["strategies"][strategy]["scenarios"][str(cfg["base_price_markup"])][period]
            require(summary["days"] == 30 and summary["fills"] == len(selected) and summary["attempts"] == len(requested), "Weather summary counts differ")
            require(summary["attempt_outcomes"] == dict(Counter(a["reason"] for a in requested)), "Weather attempt outcome count differs")
            for key, value in (("net_pnl", sum(f["pnl"] for f in selected)), ("fees", sum(f["fees"] for f in selected)), ("deployed_cost", sum(f["cost"] for f in selected))):
                close(summary[key], value, "Weather summary " + key)
            close(summary["ending_cash"], cfg["initial_capital"] + summary["net_pnl"], "Weather ending cash")
            if not selected:
                require(summary["three_day_block_bootstrap_pnl_95pct"] == [None, None] and summary["cost_roi_pct"] is None, "Zero weather fills must not imply financial certainty")
    return {"days": 60, "markets": 660, "raw_trade_rows": raw_count, "base_fills_checked": len(fills),
            "report_sha256": digest(output / "report.json"), "strategy_verdicts": {s: r["verdict"] for s, r in report["strategies"].items()},
            "scope": "Base fill records and summary mathematics verified; stress fills are not separately persisted."}


def weather_full_books():
    output = ROOT / "results/weather_full_books"
    report = read(output / "summary.json")
    cfg_path = ROOT / "research/weather_full_book_config.json"
    cfg = read(cfg_path)
    require(report["config"] == cfg and report["config_sha256"] == digest(cfg_path), "Full weather configuration hash mismatch")
    require(report["days"] == 30 and report["markets"] == 330 and report["outcome_snapshots"] == 660,
            "Full weather 30-day/330-market/660-outcome scope differs")
    require(report["date_range"] == ["2026-08-31", "2026-09-29"] and report["untouched_holdout"] is False
            and report["live_execution_verified"] is False, "Full weather period or evidence claim changed")
    input_path = output / "input_manifest.json"
    require(report["input_manifest_sha256"] == digest(input_path), "Full weather input manifest mismatch")
    inputs = read(input_path)["files"]
    for entry in inputs:
        path = (ROOT / entry["path"]).resolve()
        require(path.is_relative_to(ROOT.resolve()), "Full weather input outside project")
        if digest(path) != entry["sha256"]:
            raise Pending("Full weather input changed: " + entry["path"])
    fresh(output / "summary.json", [ROOT / e["path"] for e in inputs])
    manifest = read(ROOT / "research/weather_full_book_data/manifest.json")
    require(manifest["complete"] and not manifest["errors"] and manifest["completed_days"] == 30
            and manifest["market_count"] == 330 and manifest["outcome_snapshots"] == 660, "Full weather acquisition incomplete")
    snapshots_path = ROOT / manifest["snapshots_path"]
    require(digest(snapshots_path) == manifest["snapshots_sha256"] == report["data_sha256"], "Full weather snapshots hash mismatch")
    for day in manifest["days"]:
        require(day["market_count"] == 11 and day["outcome_count"] == 22, "Incomplete full weather acquisition day")
        for part in day["partitions"]:
            require(digest(ROOT / part["subset_path"]) == part["subset_sha256"], "Weather archive subset hash mismatch")
    rows = [json.loads(line) for line in snapshots_path.read_text(encoding="utf-8").splitlines() if line]
    snapshots = {(r["date"], r["condition_id"], r["outcome"]): r for r in rows}
    require(len(snapshots) == len(rows) == 660 and len({r["date"] for r in rows}) == 30, "Full weather duplicate or missing snapshots")
    total_fills = 0
    require(len(report["scenarios"]) == 9, "Full weather sensitivity grid incomplete")
    for scenario in report["scenarios"].values():
        path = output / scenario["result_file"]
        require(digest(path) == scenario["result_sha256"], "Full weather scenario hash mismatch")
        result = read(path)
        fills, attempts, candidates, daily = (result[key] for key in ("fills", "attempts", "candidates", "daily"))
        age, markup = scenario["max_quote_age_seconds"], scenario["price_markup"]
        require(age in (5, 60, 300) and markup in (.01, .025, .05), "Unfrozen full weather scenario")
        require(len(candidates) == 660 and len(daily) == 30, "Full weather candidate/day count differs")
        require(len({a["date"] for a in attempts}) == len(attempts) and len({f["date"] for f in fills}) == len(fills), "More than one weather event attempt/fill")
        require(scenario["attempts"] == len(attempts) and scenario["fills"] == len(fills)
                and scenario["wins"] == sum(f["settlement"] == 1 for f in fills), "Full weather counts differ")
        require(scenario["attempt_outcomes"] == dict(Counter(a["reason"] for a in attempts)), "Full weather rejection counts differ")
        for attempt in attempts:
            eligible = [c for c in candidates if c["date"] == attempt["date"] and c["signal_reason"] == "eligible"]
            best = max(eligible, key=lambda c: (c["estimated_net_edge"], c["condition_id"], c["outcome"]))
            require((best["condition_id"], best["outcome"]) == (attempt["condition_id"], attempt["outcome"]), "Future execution affected weather candidate rank")
        for fill in fills:
            row = snapshots[(fill["date"], fill["condition_id"], fill["outcome"])]
            require(row["token_id"] == fill["token_id"], "Weather distinct token identity mismatch")
            require(fill["fill_timestamp"] == fill["decision_timestamp"] + cfg["execution_latency_seconds"]
                    and fill["fill_timestamp"] < min(fill["close_timestamp"], fill["resolution_timestamp"]), "Full weather fill chronology failure")
            for name, timestamp in (("signal", fill["decision_timestamp"]), ("execution", fill["fill_timestamp"])):
                book = row[name]
                require(book["book_book_valid"] and book["timestamp"] == timestamp and book["book_ask_size"] >= fill["shares"], "Full weather actual depth insufficient")
                require(0 < timestamp - book["book_quote_recv_ts"] <= age and 0 <= timestamp - book["book_quote_source_ts"] <= age, "Full weather future/stale quote")
            close(fill["entry_price"], row["execution"]["book_ask"] + markup, "Full weather adverse ask cost")
            require(fill["entry_price"] <= row["signal"]["book_ask"] + cfg["max_price_move"] + 1e-10, "Full weather price cap")
            require(fill["shares"] == cfg["order_shares"], "Full weather order size changed")
            record = next(r for r in read(ROOT / "data/weather/resolutions" / (fill["condition_id"] + ".json"))["body"]["data"] if r["status"] == "resolved" and r["condition_id"] == fill["condition_id"])
            yes = int(record["price"]) / 1e18
            payout = fill["shares"] * (yes if fill["outcome"] == "Yes" else 1 - yes)
            require(record["transaction_hash"] == fill["resolution_transaction_hash"], "Full weather chain resolution differs")
            close(fill["fees"], fee(fill["shares"], fill["entry_price"], cfg["current_taker_fee_rate"]), "Full weather fee", 1e-8)
            close(fill["cost"], fill["shares"] * fill["entry_price"] + fill["fees"], "Full weather cost")
            close(fill["payout"], payout, "Full weather payout")
            close(fill["pnl"], payout - fill["cost"], "Full weather PnL")
        for key, value in (("net_pnl", sum(f["pnl"] for f in fills)), ("fees", sum(f["fees"] for f in fills)), ("deployed_cost", sum(f["cost"] for f in fills))):
            close(scenario[key], value, "Full weather summary " + key)
        cash, minimum = cfg["initial_capital"], cfg["initial_capital"]
        ledger = [(f["fill_timestamp"], 1, -f["cost"]) for f in fills] + [(f["resolution_timestamp"], 0, f["payout"]) for f in fills]
        for _, _, change in sorted(ledger):
            cash += change
            minimum = min(minimum, cash)
            require(cash >= -1e-8, "Full weather cash overdrawn")
        close(cash, scenario["ending_cash"], "Full weather final settled cash")
        close(minimum, scenario["minimum_cash"], "Full weather minimum cash")
        total_fills += len(fills)
    return {"days": 30, "markets": 330, "outcome_snapshots": 660, "fills_checked_across_scenarios": total_fills,
            "summary_sha256": digest(output / "summary.json"), "verdict": report["verdict"], "untouched_holdout": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "allow_pending": args.allow_pending,
              "scope": "Artifact provenance and arithmetic, not a certification of profitability, strategy selection, live fills or full-day coverage.",
              "financial_success_certified": False, "checks": {}}
    for name, function in (("reference_sources", sources), ("btc_local", btc), ("btc_expanded_30_days", lambda: btc(True)), ("weather_60_days", weather), ("weather_full_books_30_days", weather_full_books)):
        try:
            report["checks"][name] = {"status": "passed", **function()}
        except (FileNotFoundError, Pending) as exc:
            report["checks"][name] = {"status": "pending", "reason": str(exc)}
        except (ValueError, KeyError, TypeError, OSError, IndexError, StopIteration) as exc:
            report["checks"][name] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    states = [entry["status"] for entry in report["checks"].values()]
    report["passed"] = all(state == "passed" for state in states)
    report["validation_complete"] = "pending" not in states
    report["status"] = "passed" if report["passed"] else "failed" if "failed" in states else "pending"
    destination = ROOT / "research/artifact_validation.json"
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] or (args.allow_pending and "failed" not in states) else 1


if __name__ == "__main__":
    raise SystemExit(main())
