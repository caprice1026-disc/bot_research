"""Independently reconcile saved cash/trade ledgers and compare deterministic outputs."""
import argparse
import csv
from decimal import Decimal as D
import json
from pathlib import Path

from swingguard.data import BAR_MS, date_ms, sha, write_json


def verify(output):
    manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
    for relative, digest in manifest.items():
        if sha(output / relative) != digest:
            raise ValueError("artifact mismatch: " + relative)
    freeze = json.loads((output / "freeze.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    initial = D(freeze["config"]["initial_cash"])
    counts = {}
    for period, item in summary["periods"].items():
        start, end = map(date_ms, item["bounds"])
        for arm, metrics in item["arms"].items():
            directory = output / period / arm
            cash, fees, slip, turnover = initial, D(0), D(0), D(0)
            quantities = {s: D(0) for s in freeze["config"]["symbols"]}
            fills = 0
            with (directory / "trades.csv").open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    qty, price, fee = D(row["quantity"]), D(row["fill_price"]), D(row["commission"])
                    assert qty > 0 and price > 0
                    signed = qty if row["side"] == "buy" else -qty
                    cash -= signed * price + fee
                    quantities[row["symbol"]] += signed
                    assert abs(cash - D(row["cash_after"])) < D("1e-12")
                    assert quantities[row["symbol"]] == D(row["quantity_after"])
                    assert quantities[row["symbol"]] >= 0
                    assert cash >= D("-1e-12")
                    if row["timing"] == "open":
                        assert int(row["event_time_ms"]) == int(row["bar_open_ms"])
                    if row["timing"] == "intrabar_unknown":
                        assert int(row["event_time_ms"]) == int(row["bar_open_ms"]) + BAR_MS
                    fees += fee
                    slip += D(row["slippage_cost"])
                    turnover += qty * price
                    fills += 1
            assert all(q == 0 for q in quantities.values())
            for actual, expected in ((cash, metrics["final_equity"]), (fees, metrics["commission"]),
                                     (slip, metrics["slippage_cost"]), (turnover, metrics["turnover_notional"])):
                assert abs(actual - D(str(expected))) < D("1e-7")
            assert fills == metrics["fill_count"]
            peak, drawdown, rows = initial, D(0), 0
            with (directory / "equity.csv").open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    assert int(row["time_ms"]) == start + (rows + 1) * BAR_MS
                    assert D(row["cash"]) >= D("-1e-12")
                    assert 0 <= D(row["exposure"]) <= 1
                    equity = D(row["equity"])
                    peak = max(peak, equity)
                    drawdown = max(drawdown, 1 - equity / peak)
                    rows += 1
            assert rows == (end - start) // BAR_MS
            assert abs(equity - cash) < D("1e-12")
            assert abs(drawdown * 100 - D(str(metrics["max_drawdown_pct"]))) < D("1e-8")
            counts[period + "/" + arm] = {"equity_rows": rows, "fills": fills, "cash_ledger": "matched"}
    return {"status": "passed", "artifact_hashes_checked": len(manifest), "arms": counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--economics-only", action="store_true",
                        help="Compare outputs and metrics across metadata-only/API guard revisions")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    evidence = verify(args.output)
    if args.compare:
        verify(args.compare)
        names = [] if args.economics_only else ["summary.json", "freeze.json"]
        names += [p.relative_to(args.output).as_posix() for p in args.output.rglob("*.csv")]
        names += [p.relative_to(args.output).as_posix() for p in args.output.rglob("decisions.jsonl")]
        names += [p.relative_to(args.output).as_posix() for p in args.output.rglob("risk_transitions.json")]
        for relative in names:
            assert sha(args.output / relative) == sha(args.compare / relative), relative
        if args.economics_only:
            actual = json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
            reference = json.loads((args.compare / "summary.json").read_text(encoding="utf-8"))
            assert actual["periods"] == reference["periods"]
        evidence["reproducibility"] = {"status": "byte_identical", "files_compared": len(names),
                                        "scope": "economic_outputs_and_metrics" if args.economics_only else "full_deterministic_outputs",
                                        "note": "proposal_audit cache_hit metadata intentionally differs on cache reuse"}
    if args.evidence:
        write_json(args.evidence, evidence)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
