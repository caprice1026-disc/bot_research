"""Exploratory past-seven-day persistence control using identical execution gates."""
from datetime import date, timedelta
import statistics

from weather_deep_research import CONFIG, DATA, OUTPUT, ROOT, calibrate, day_path, load_city, read_json, sha256, write_json
from weather_backtest import load_days, load_observations
from weather_full_book_backtest import run_scenario


def persistence_days(days):
    highs = {d["date"]: d["actual_iem_high"] for d in days}
    rows = []
    for d in sorted(days, key=lambda d: d["date"]):
        at = date.fromisoformat(d["date"])
        previous = [(at - timedelta(days=i)).isoformat() for i in range(1, 8)]
        if all(ds in highs for ds in previous):
            rows.append({**d, "model_maxes": {"persistence": statistics.mean(highs[ds] for ds in previous)}})
    return rows


def main():
    cfg = read_json(CONFIG)
    manifest = read_json(DATA / "books-manifest.json")
    result = {"study_type": "exploratory control added after new primary evaluation; no primary rule changed",
              "config_sha256": sha256(CONFIG), "primary_summary_sha256": sha256(OUTPUT / "summary.json"), "cities": {}}
    for p in cfg["profiles"]:
        days, _ = load_city(p, cfg)
        if p["city"] == "chicago":
            original_cfg = read_json(ROOT / "research/weather_config.json")
            old, _ = load_days(original_cfg, load_observations(original_cfg))
            days = list({d["date"]: d for d in [*old, *days]}.values())
        forecasts = persistence_days(days)
        fit = calibrate(forecasts, {**cfg, "forecast_models": ["persistence"]})
        evaluation = [d for d in forecasts if d["date"] >= p["evaluation_start"]]
        expected = len([d for d in days if d["date"] >= p["evaluation_start"]])
        if len(evaluation) != expected:
            raise ValueError("Missing prior-seven-day history; do not shorten benchmark scope")
        source = [r for meta in manifest["days"] if meta["city"] == p["city"] for r in read_json(ROOT / meta["snapshot_path"])]
        snapshots = {(r["date"], r["condition_id"], r["outcome"]): {**r, "signal": r["states"]["0"], "execution": r["states"]["60"]} for r in source}
        actual = {**cfg, "initial_capital": cfg["initial_capital_per_city"], "decision_utc_hour": cfg["decision_local_hour"] - p["utc_offset_hours"]}
        replay = run_scenario(evaluation, snapshots, actual, fit, 60, .01)
        write_json(OUTPUT / p["city"] / "persistence_control.json", replay)
        result["cities"][p["city"]] = {"fit": fit, "summary": replay["summary"], "result_sha256": sha256(OUTPUT / p["city"] / "persistence_control.json")}
    result["aggregate_pnl"] = round(sum(c["summary"]["net_pnl"] for c in result["cities"].values()), 6)
    result["aggregate_fills"] = sum(c["summary"]["fills"] for c in result["cities"].values())
    write_json(OUTPUT / "persistence_control.json", result)
    print({c: (v["summary"]["fills"], v["summary"]["net_pnl"]) for c, v in result["cities"].items()})


if __name__ == "__main__":
    main()
