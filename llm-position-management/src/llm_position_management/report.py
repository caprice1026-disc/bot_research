"""Small, explicit summary for an offline position-management run."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .runner import RunnerResult


def build_report(result: RunnerResult) -> dict[str, Any]:
    statuses = Counter(record.status for record in result.records)
    snapshot = result.final_snapshot
    return {
        "status": "ok",
        "decision_count": len(result.records),
        "fill_count": result.fill_count,
        "statuses": dict(sorted(statuses.items())),
        "final_equity": str(snapshot.equity),
        "final_signed_quantity": str(snapshot.signed_quantity),
        "realized_pnl": str(snapshot.realized_pnl),
        "unrealized_pnl": str(snapshot.unrealized_pnl),
        "fees_paid": str(snapshot.fees_paid),
        "funding_paid": str(snapshot.funding_paid),
        "model_cost_usd": str(result.model_cost_usd),
    }


def render_report_markdown(report: dict[str, Any]) -> str:
    lines = ["# Position-management offline replay", ""]
    for key in (
        "decision_count",
        "fill_count",
        "final_equity",
        "final_signed_quantity",
        "realized_pnl",
        "unrealized_pnl",
        "fees_paid",
        "funding_paid",
        "model_cost_usd",
    ):
        lines.append(f"- {key}: {report[key]}")
    lines.extend(["", "## Decision statuses", ""])
    for status, count in report["statuses"].items():
        lines.append(f"- {status}: {count}")
    lines.append("")
    return "\n".join(lines)
