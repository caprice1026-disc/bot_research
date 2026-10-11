"""Independently check exploratory control against raw books and official payout."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from weather_deep_research import CONFIG, DATA, OUTPUT, load_city, read_json, sha256, write_json


def main():
    cfg = read_json(CONFIG)
    control = read_json(OUTPUT / "persistence_control.json")
    if control["config_sha256"] != sha256(CONFIG) or control["primary_summary_sha256"] != sha256(OUTPUT / "summary.json"):
        raise ValueError("Control/config/primary identity changed")
    manifest = read_json(DATA / "books-manifest.json")
    checked, total, daily = 0, Decimal(0), defaultdict(float)
    for profile in cfg["profiles"]:
        city = profile["city"]
        record = control["cities"][city]
        path = OUTPUT / city / "persistence_control.json"
        if sha256(path) != record["result_sha256"]:
            raise ValueError("Control output changed")
        replay = read_json(path)
        days, _ = load_city(profile, cfg)
        markets = {(d["date"], m["condition_id"]): m for d in days for m in d["markets"]}
        raw = {(r["date"], r["condition_id"], r["outcome"]): r for d in manifest["days"]
               if d["city"] == city for r in read_json(ROOT / d["snapshot_path"])}
        if record["fit"]["through"] != cfg["calibration_end"] or record["fit"]["days"] != 23:
            raise ValueError("Control calibration period changed")
        if len(replay["daily"]) != len([d for d in days if d["date"] >= profile["evaluation_start"]]):
            raise ValueError("Control shortened evaluation period")
        if len({a["date"] for a in replay["attempts"]}) != len(replay["attempts"]):
            raise ValueError("Multiple attempts per day")
        for attempt in replay["attempts"]:
            eligible = [c for c in replay["candidates"] if c["date"] == attempt["date"] and c["signal_reason"] == "eligible"]
            best = max(eligible, key=lambda c: (c["estimated_net_edge"], c["condition_id"], c["outcome"]))
            if (best["condition_id"], best["outcome"]) != (attempt["condition_id"], attempt["outcome"]):
                raise ValueError("Execution result changed control selection")
        for f in replay["fills"]:
            r = raw[(f["date"], f["condition_id"], f["outcome"])]
            m = markets[(f["date"], f["condition_id"])]
            if f["token_id"] != r["token_id"] or r["token_id"] != m["tokens"][f["outcome"]]:
                raise ValueError("Wrong control token")
            for delay in ("0", "60"):
                b = r["states"][delay]
                if not b["book_book_valid"] or b["book_ask_size"] < f["shares"]:
                    raise ValueError("Control book invalid/insufficient")
                if not 0 < b["timestamp"] - b["book_quote_recv_ts"] <= 60 or not 0 <= b["timestamp"] - b["book_quote_source_ts"] <= 60:
                    raise ValueError("Control book stale/future")
            p = Decimal(str(round(r["states"]["60"]["book_ask"] + .01, 10)))
            shares = Decimal(str(f["shares"]))
            fee = (shares * Decimal(str(cfg["current_taker_fee_rate"])) * p * (1 - p)).quantize(Decimal(".00001"), rounding=ROUND_HALF_UP)
            cost = shares * p + fee
            winner = m["yes_settlement"] if f["outcome"] == "Yes" else 1 - m["yes_settlement"]
            payout = shares * Decimal(str(winner))
            if f["shares"] != 5 or f["fill_timestamp"] != r["decision_timestamp"] + 60 or f["fill_timestamp"] >= min(m["close_timestamp"], m["resolution_timestamp"]):
                raise ValueError("Control timing/size failure")
            if float(p) > r["states"]["0"]["book_ask"] + cfg["max_price_move"] + 1e-9:
                raise ValueError("Control price cap failed")
            if max(abs(float(cost) - f["cost"]), abs(float(fee) - f["fees"]), abs(float(payout) - f["payout"]), abs(float(payout - cost) - f["pnl"])) > 1e-7:
                raise ValueError("Control fee/cost/payout mismatch")
            total += payout - cost
            checked += 1
        ledger = [(f["fill_timestamp"], 1, -f["cost"]) for f in replay["fills"]] + [(f["resolution_timestamp"], 0, f["payout"]) for f in replay["fills"]]
        cash = minimum = cfg["initial_capital_per_city"]
        for _, _, value in sorted(ledger):
            cash += value
            minimum = min(minimum, cash)
        if minimum < 0 or max(abs(cash - record["summary"]["ending_cash"]), abs(minimum - record["summary"]["minimum_cash"])) > 1e-5:
            raise ValueError("Control cash ledger differs")
        for row in replay["daily"]:
            actual = sum(f["pnl"] for f in replay["fills"] if f["date"] == row["date"])
            if abs(actual - row["pnl"]) > 1e-7:
                raise ValueError("Control daily attribution differs")
            daily[row["date"]] += actual
    if checked != control["aggregate_fills"] or abs(float(total) - control["aggregate_pnl"]) > 1e-7 or len(daily) != 34:
        raise ValueError("Control aggregate differs")
    write_json(OUTPUT / "control_validation.json", {"passed": True, "fills_checked": checked,
               "calendar_dates": len(daily), "control_sha256": sha256(OUTPUT / "persistence_control.json"),
               "financial_success_certified": False})
    print("Verified", checked, "control fills and", len(daily), "paired dates")


if __name__ == "__main__":
    main()
