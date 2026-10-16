"""Independent verification of saved artifacts. Reads no OHLCV/holdout panel."""
from pathlib import Path
import argparse
import csv
import json
from datetime import datetime


def audit(root):
    root = Path(root)
    result = []
    for directory in sorted((root / "backtests").iterdir()):
        stats = json.loads((directory / "metrics.json").read_text())
        with (directory / "account_movements.csv").open(newline="") as handle:
            movements = list(csv.DictReader(handle))
        with (directory / "trades.csv").open(newline="") as handle:
            trades = list(csv.DictReader(handle))
        with (directory / "equity.csv").open(newline="") as handle:
            equity = list(csv.DictReader(handle))
        cash, shares, cost = stats["initial_equity"], {}, 0.0
        previous_time = None
        for row in movements:
            when = datetime.fromisoformat(row["timestamp"])
            if previous_time and when < previous_time:
                raise AssertionError("Nonchronological saved movements")
            previous_time = when
            if row["signal_available_at"] and directory.name != "buy_hold":
                elapsed = (when - datetime.fromisoformat(row["signal_available_at"])).total_seconds()
                if elapsed < 3600:
                    raise AssertionError("Signal unavailable at fill")
            q, p, fee = float(row["quantity_delta"]), float(row["price"]), float(row["fee"])
            cash -= q * p + fee
            shares[row["symbol"]] = shares.get(row["symbol"], 0.0) + q
            cost += fee
            if cash < -1e-6 or min(shares.values()) < -1e-6:
                raise AssertionError("Saved ledger borrows or shorts")
        if abs(cash - stats["final_equity"]) > 1e-6 or any(abs(q) > 1e-6 for q in shares.values()):
            raise AssertionError("Final saved quantity/cash reconciliation failed")
        if abs(cost - stats["cost"]) > 1e-6 or len(trades) != stats["trades"]:
            raise AssertionError("Saved costs/count mismatch")
        cumulative_pnl = sum(float(r["holding_pnl"]) for r in equity)
        cumulative_fees = sum(float(r["fees"]) for r in equity)
        if abs(stats["initial_equity"] + cumulative_pnl - cumulative_fees - float(equity[-1]["equity"])) > 1e-6:
            raise AssertionError("Saved equity PnL/fee conservation failed")
        result.append({"arm": directory.name, "passed": True, "cash_residual": cash - stats["final_equity"],
                       "full_movement_rows": len(movements), "material_trades": len(trades)})
    output = {"passed": True, "accounts": result, "reads_holdout_prices": False,
              "cash_quantity_tolerance": 1e-6, "test_batch_recovery": (root / "recovery.json").exists()}
    (root / "independent_saved_ledger_audit.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"saved_ledger_audit": "passed", "accounts": len(result),
                      "max_cash_residual": max(abs(x["cash_residual"]) for x in result)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    audit(parser.parse_args().output)
