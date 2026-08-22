from __future__ import annotations

from base_lp.backtest.models import BacktestResult


def summarize_result(result: BacktestResult, hodl_terminal_value_usd: float) -> dict[str, float | int | str]:
    values = [point.value_usd for point in result.equity_curve]
    if not values:
        raise ValueError("cannot summarize an empty equity curve")
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    return {
        "strategy": result.strategy_name,
        "terminal_value_usd": result.terminal_value_usd,
        "net_return": result.terminal_value_usd / values[0] - 1.0,
        "fees_usd": result.total_fees_usd,
        "costs_usd": result.total_costs_usd,
        "hodl_alpha_usd": result.terminal_value_usd - hodl_terminal_value_usd,
        "max_drawdown_usd": max_drawdown,
        "time_in_range": sum(point.in_range for point in result.equity_curve) / len(result.equity_curve),
        "rebalances": sum(1 for entry in result.action_log if entry.get("action") == "reset"),
        "gross_fee_to_cost": result.total_fees_usd / result.total_costs_usd
        if result.total_costs_usd
        else None,
    }
