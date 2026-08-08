"""Write and validate inspectable backtest artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .backtest import BacktestResult


def _trade_row(trade: Any) -> dict[str, Any]:
    return {
        "market_id": trade.market_id,
        "trade_time": trade.trade_time.isoformat(),
        "model_probability": trade.model_probability,
        "observed_ask": trade.observed_ask,
        "executable_price": trade.executable_price,
        "outcome_yes": trade.outcome_yes,
        "quantity": trade.quantity,
        "gross_pnl": trade.gross_pnl,
        "net_pnl": trade.net_pnl,
        "net_edge": trade.net_edge,
    }


def write_backtest_artifacts(result: BacktestResult, results_dir: Path, report_path: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    rows = [_trade_row(trade) for trade in result.trades]
    summary = {
        "status": result.status.value,
        "reason": result.reason,
        "trade_count": len(rows),
        "gross_pnl": sum(row["gross_pnl"] for row in rows),
        "net_pnl": sum(row["net_pnl"] for row in rows),
    }
    (results_dir / "backtest_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    fieldnames = [
        "market_id",
        "trade_time",
        "model_probability",
        "observed_ask",
        "executable_price",
        "outcome_yes",
        "quantity",
        "gross_pnl",
        "net_pnl",
        "net_edge",
    ]
    with (results_dir / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                "# Backtest Report",
                "",
                f"- Status: `{summary['status']}`",
                f"- Reason: {summary['reason'] or 'none'}",
                f"- Trade count: {summary['trade_count']}",
                f"- Gross PnL: {summary['gross_pnl']:.8f}",
                f"- Net PnL: {summary['net_pnl']:.8f}",
                "",
                "この結果は実データの取得件数とPoint-in-Time検証結果と併せて解釈する。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def validate_results(results_dir: Path) -> dict[str, Any]:
    summary_path = results_dir / "backtest_summary.json"
    trades_path = results_dir / "trades.csv"
    if not summary_path.exists() or not trades_path.exists():
        return {"valid": False, "reason": "required result artifact is missing"}
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        with trades_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        return {"valid": False, "reason": str(exc)}
    required = {"status", "reason", "trade_count", "gross_pnl", "net_pnl"}
    if not required.issubset(summary):
        return {"valid": False, "reason": "backtest summary is missing required fields"}
    if summary["trade_count"] != len(rows):
        return {"valid": False, "reason": "trade_count does not match trades.csv"}
    return {"valid": True, "status": summary["status"], "trade_count": len(rows)}
