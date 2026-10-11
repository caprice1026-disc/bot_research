"""Date-clustered uncertainty and changes to trade selection, from saved artifacts."""
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random
import statistics

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results/weather_deep"
report = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
cfg = report["config"]


def block_bootstrap(values, block):
    rng, totals = random.Random(cfg["bootstrap_seed"]), []
    for _ in range(cfg["bootstrap_samples"]):
        draw = []
        while len(draw) < len(values):
            start = rng.randrange(len(values))
            draw.extend(values[(start + j) % len(values)] for j in range(block))
        totals.append(sum(draw[:len(values)]))
    totals.sort()
    return {"total_pnl_95pct": [round(totals[int(len(totals) * .025)], 6), round(totals[int(len(totals) * .975)], 6)],
            "fraction_bootstrap_pnl_le_zero": sum(v <= 0 for v in totals) / len(totals)}


def main():
    daily = json.loads((OUT / "primary_daily.json").read_text())
    values = [r["pnl"] for r in daily]
    result = {"scope": "Exploratory robustness; descriptive bootstrap tails are not frequentist p-values",
              "joint_calendar_dates": len(values), "block_lengths": {str(b): block_bootstrap(values, b) for b in (1, 3, 5, 7, 10)}}
    if result["block_lengths"]["3"]["total_pnl_95pct"] != report["aggregate_primary"]["three_day_block_bootstrap_pnl_95pct"]:
        raise ValueError("Independent date bootstrap differs from primary summary")
    ordered = sorted(values, reverse=True)
    result["new_pnl_without_best_day"] = round(sum(values) - ordered[0], 6)
    result["new_pnl_without_best_three_days"] = round(sum(values) - sum(ordered[:3]), 6)
    fields = list(report["city_results"]["nyc"]["scenarios"])
    aggregates = {}
    for name in fields:
        combined, fills = defaultdict(float), []
        for city in report["city_results"]:
            case = json.loads((OUT / city / (name + ".json")).read_text())
            for row in case["daily"]:
                combined[row["date"]] += row["pnl"]
            fills.extend(case["fills"])
        aggregates[name] = {"fills": len(fills), "pnl": round(sum(combined.values()), 6),
            "three_day_pnl_95pct": block_bootstrap([combined[ds] for ds in sorted(combined)], 3)["total_pnl_95pct"] if fills else [None, None]}
    result["scenario_aggregate"] = aggregates
    selected = json.loads((OUT / "primary_fills.json").read_text())
    result["mean_selected_probability"] = statistics.mean(f["model_probability"] for f in selected)
    result["actual_win_fraction"] = statistics.mean(f["settlement"] for f in selected)
    contrasts = {}
    for city in report["city_results"]:
        base = json.loads((OUT / city / "primary.json").read_text())
        strict = json.loads((OUT / city / "age_5.json").read_text())
        strict_keys = {(f["date"], f["condition_id"], f["outcome"]) for f in strict["fills"]}
        retained = [f for f in base["fills"] if (f["date"], f["condition_id"], f["outcome"]) in strict_keys]
        fixed_fresh = [f for f in base["fills"] if max(f["decision_timestamp"] - f["signal_quote_recv_ts"],
            f["decision_timestamp"] - f["signal_quote_source_ts"], f["fill_timestamp"] - f["execution_quote_recv_ts"],
            f["fill_timestamp"] - f["execution_quote_source_ts"]) <= 5]
        contrasts[city] = {"common_trades": len(retained), "common_primary_pnl": round(sum(f["pnl"] for f in retained), 6),
            "fixed_primary_fresh_5_fills": len(fixed_fresh), "fixed_primary_fresh_5_pnl": round(sum(f["pnl"] for f in fixed_fresh), 6)}
    result["freshness_trade_set_contrasts"] = contrasts
    control = json.loads((OUT / "persistence_control.json").read_text())
    if control["primary_summary_sha256"] != hashlib.sha256((OUT / "summary.json").read_bytes()).hexdigest():
        raise ValueError("Control refers to a different primary result")
    control_daily = defaultdict(float)
    for city, record in control["cities"].items():
        path = OUT / city / "persistence_control.json"
        if record["result_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Control result changed")
        case = json.loads(path.read_text())
        for row in case["daily"]:
            control_daily[row["date"]] += row["pnl"]
    if set(control_daily) != {r["date"] for r in daily}:
        raise ValueError("Control and primary calendar scopes differ")
    paired = [{"date": r["date"], "forecast_pnl": r["pnl"], "persistence_pnl": control_daily[r["date"]],
               "incremental_pnl": r["pnl"] - control_daily[r["date"]]} for r in daily]
    result["persistence_control"] = {
        "study_type": control["study_type"], "fills": control["aggregate_fills"],
        "pnl": control["aggregate_pnl"],
        "three_day_pnl_95pct": block_bootstrap([r["persistence_pnl"] for r in paired], 3)["total_pnl_95pct"],
        "forecast_minus_control_pnl": round(sum(r["incremental_pnl"] for r in paired), 6),
        "paired_three_day_incremental_pnl_95pct": block_bootstrap([r["incremental_pnl"] for r in paired], 3)["total_pnl_95pct"],
        "primary_summary_sha256": control["primary_summary_sha256"],
    }
    with (OUT / "paired_control_daily.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(paired[0]))
        writer.writeheader()
        writer.writerows(paired)
    with (OUT / "primary_fills.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    (OUT / "statistical_robustness.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
