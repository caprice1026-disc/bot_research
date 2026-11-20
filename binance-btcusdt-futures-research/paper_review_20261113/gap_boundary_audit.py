"""Record a known failure in the legacy liquidation proxy, without changing it.

Run from the repository root with Python 3.10 and the study dependencies:
    python -X utf8 -B binance-btcusdt-futures-research/paper_review_20261113/gap_boundary_audit.py

Exit code 1 is intentional when the known failed safety condition is reproduced.
This script runs one three-minute synthetic fixture. It does not rerun any
historical backtest or modify the legacy study, its reports, or its manifests.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np


HERE = Path(__file__).resolve().parent
RESEARCH_ROOT = HERE.parent
REPO_ROOT = RESEARCH_ROOT.parent
LEGACY = RESEARCH_ROOT / "paper_validation_20261031"
OUTPUT = HERE / "results" / "gap_boundary_audit.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_saved_results() -> dict:
    """Inspect saved summary rows only; never invoke the historical runners."""
    files = [
        LEGACY / "results" / "performance.csv",
        LEGACY / "results" / "supplement" / "performance.csv",
        LEGACY / "results" / "leverage_stress" / "performance.csv",
    ]
    summaries = []
    for path in files:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        negative_rows, liquidation_rows, rounded_total_loss_rows = [], [], []
        for index, row in enumerate(rows, start=2):
            return_pct = float(row["return_pct"])
            liquidation_count = int(row["liquidation_proxy_count"])
            identity = {
                "csv_line": index,
                "method": row["method"],
                "return_pct": return_pct,
                "net_pnl_usdt": float(row["net_pnl_usdt"]),
                "liquidation_proxy_count": liquidation_count,
            }
            for name in ("slippage_bps", "delay_minutes", "exposure_multiplier"):
                if name in row:
                    identity[name] = float(row[name])
            # All recorded initial capitals are positive. A reported return
            # below -100% therefore implies negative ending equity. An exactly
            # -100% rounded return cannot distinguish zero from tiny equity.
            if return_pct < -100.0:
                negative_rows.append(identity)
            if return_pct == -100.0:
                rounded_total_loss_rows.append(identity)
            if liquidation_count:
                liquidation_rows.append(identity)
        summaries.append({
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256(path),
            "rows": len(rows),
            "minimum_reported_return_pct": min(float(row["return_pct"]) for row in rows),
            "negative_final_equity_implied_by_reported_return_rows": negative_rows,
            "exactly_minus_100_percent_rounded_rows": rounded_total_loss_rows,
            "recorded_liquidation_proxy_count_sum": sum(int(row["liquidation_proxy_count"]) for row in rows),
            "rows_with_recorded_liquidation_proxy_events": liquidation_rows,
            "recorded_near_liquidation_minutes_sum": sum(int(row["near_liquidation_minutes"]) for row in rows),
        })
    return {
        "inspection_scope": "Saved CSV summaries only; no historical replay or event-path audit.",
        "negative_equity_detection": "return_pct < -100 with positive initial capital; precision is limited to the saved summary.",
        "limitation": "No negative reported ending equity or recorded liquidation events does not exclude missed intraperiod insolvency or other latent errors. Liquidation counts are model outputs, not observed exchange events.",
        "files": summaries,
        "total_rows": sum(item["rows"] for item in summaries),
    }


def main() -> int:
    sys.dont_write_bytecode = True
    source = LEGACY / "engine.py"
    source_hash = sha256(source)
    module_name = "legacy_gap_boundary_audit_engine"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the existing legacy engine")
    engine = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = engine
    spec.loader.exec_module(engine)

    prices = np.array([100.0, 40.0, 40.0])
    market = {
        name: prices.copy()
        for name in ("open", "high", "low", "close", "mark_open", "mark_high", "mark_low")
    }
    market["funding_rate"] = np.zeros(3)
    config = {
        "initial_equity": 10000.0,
        "fee_bps": 0.0,
        "maintenance_margin_rate_proxy": 0.005,
        "near_liquidation_margin_multiple": 2.0,
    }
    execution = [0, 1]
    targets = [2.0, 0.0]

    # Independent arithmetic before the boundary-flat order, not an estimate
    # obtained from the engine's own liquidation flag or post-order quantity.
    old_quantity = targets[0] * config["initial_equity"] / prices[0]
    pre_order_equity = config["initial_equity"] + old_quantity * (prices[1] - prices[0])
    pre_order_maintenance = abs(old_quantity) * prices[1] * config["maintenance_margin_rate_proxy"]
    expected_breach = pre_order_equity <= pre_order_maintenance

    equity, exposure, quantity, totals = engine.replay(
        market, execution, targets, 0, 3, config, slip_bps=0.0
    )
    observed_liquidations = int(totals[8])
    failure_reproduced = bool(
        expected_breach and observed_liquidations == 0 and equity[-1] < 0
    )
    if sha256(source) != source_hash:
        raise RuntimeError("Legacy source changed during the fixture")
    result = {
        "schema_version": 1,
        "review_date": "2026-11-13",
        "audit_status": "known_failure_reproduced" if failure_reproduced else "known_failure_not_reproduced_review_required",
        "safety_check": "failed" if failure_reproduced else "not_established",
        "exit_code": 1 if failure_reproduced else 2,
        "runner": {
            "path": Path(__file__).resolve().relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "legacy_source": {
            "path": source.relative_to(REPO_ROOT).as_posix(),
            "sha256": source_hash,
            "behavior": "_replay applies the scheduled target quantity before checking stressed equity against maintenance margin.",
        },
        "fixture": {
            "kind": "synthetic_three_minute_boundary_gap_not_historical_data",
            "prices_and_mark_prices": prices.tolist(),
            "initial_equity_usdt": config["initial_equity"],
            "execution_minute_indices": execution,
            "target_exposure_multiples": targets,
            "fee_bps": 0.0,
            "slippage_bps": 0.0,
            "funding_rates": [0.0, 0.0, 0.0],
            "maintenance_margin_rate_proxy": config["maintenance_margin_rate_proxy"],
        },
        "expected_pre_order_condition": {
            "minute_index": 1,
            "old_quantity": float(old_quantity),
            "equity_after_opening_gap_usdt": float(pre_order_equity),
            "maintenance_requirement_usdt": float(pre_order_maintenance),
            "maintenance_breach": bool(expected_breach),
            "requirement": "Recognize the pre-existing position's maintenance breach before processing an elective boundary-flat target; do not interpret the flattened position as evidence that no breach occurred.",
        },
        "observed_legacy_behavior": {
            "equity_path_usdt": equity.tolist(),
            "quantity_path": quantity.tolist(),
            "exposure_path": exposure.tolist(),
            "negative_final_equity": bool(equity[-1] < 0),
            "liquidation_proxy_count": observed_liquidations,
            "near_liquidation_minutes": int(totals[7]),
        },
        "interpretation": "This reproduces a missed gap-boundary insolvency condition in a proxy engine. It is a failing behavioral audit, not a passed safety test. It does not establish the frequency of the defect in historical data or invalidate unrelated saved PnL results by itself.",
        "saved_summary_inspection": summarize_saved_results(),
        "changes_to_legacy_study": "None: no engine, report, manifest, market input, or historical backtest output was modified.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "audit_status": result["audit_status"],
        "expected_pre_order_equity_usdt": float(pre_order_equity),
        "expected_maintenance_usdt": float(pre_order_maintenance),
        "observed_equity_path": equity.tolist(),
        "observed_liquidation_count": observed_liquidations,
        "saved_summary_rows_inspected": result["saved_summary_inspection"]["total_rows"],
        "output": OUTPUT.relative_to(REPO_ROOT).as_posix(),
        "note": "Exit 1 intentionally records the reproduced known failure; no safety pass is claimed.",
    }, ensure_ascii=False))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
