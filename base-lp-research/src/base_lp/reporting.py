from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_report(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Base Uniswap v3 LP Research Report",
        "",
        f"- Status: `{summary.get('status', 'unknown')}`",
        f"- Research sufficiency: `{summary.get('research_status', 'unknown')}`",
        f"- Strategy: `{summary.get('strategy', 'unknown')}`",
        f"- Counterfactual: `{summary.get('counterfactual_mode', 'C0')}`",
        f"- Fee precision: `{summary.get('fee_precision', 'P0')}`",
        "",
        "## Primary Results",
        "",
    ]
    keys = [
        "terminal_value_usd",
        "net_return",
        "hodl_alpha_usd",
        "fees_usd",
        "costs_usd",
        "max_drawdown_usd",
        "time_in_range",
        "rebalances",
    ]
    for key in keys:
        if key in summary:
            lines.append(f"- {key}: `{summary[key]}`")
    lines.extend(["", "## Limitations", "", "This v0.1 report uses C0 and P0 approximations. It is not an execution or investment recommendation.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
