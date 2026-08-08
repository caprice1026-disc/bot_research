from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from base_lp.backtest.engine import SimulationConfig, price_token1_per_token0, run_backtest
from base_lp.metrics.performance import summarize_result


def _canonical_config(parameters: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a JSON-safe config copy and its stable SHA-256 identifier."""

    config = json.loads(json.dumps(dict(parameters), sort_keys=True, separators=(",", ":")))
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return config, hashlib.sha256(encoded).hexdigest()


def _hodl_terminal_value(events: list[Any], simulation_config: SimulationConfig) -> float:
    first = events[0]
    final = events[-1]
    initial_price = price_token1_per_token0(
        first.sqrt_price_x96, simulation_config.token0_decimals, simulation_config.token1_decimals
    )
    final_price = price_token1_per_token0(
        final.sqrt_price_x96, simulation_config.token0_decimals, simulation_config.token1_decimals
    )
    initial_token0 = simulation_config.capital_usd / 2 / initial_price
    initial_token1 = simulation_config.capital_usd / 2
    return initial_token0 * final_price + initial_token1


def run_parameter_sweep(
    events: Iterable[Any],
    parameter_grid: Iterable[Mapping[str, Any]],
    strategy_factory: Callable[[Mapping[str, Any]], Any],
    simulation_config: SimulationConfig,
) -> list[dict[str, Any]]:
    """Run each strategy configuration in deterministic input order.

    The returned rows retain the exact normalized configuration and a hash so
    that a result can be joined to a later report without relying on row order.
    """

    event_list = list(events)
    if not event_list:
        raise ValueError("parameter sweep requires at least one event")
    hodl_value = _hodl_terminal_value(event_list, simulation_config)
    rows: list[dict[str, Any]] = []
    for raw_parameters in parameter_grid:
        parameters, config_hash = _canonical_config(raw_parameters)
        strategy = strategy_factory(parameters)
        result = run_backtest(event_list, strategy, simulation_config)
        summary = summarize_result(result, hodl_value)
        rows.append(
            {
                "config": parameters,
                "config_hash": config_hash,
                "strategy": summary["strategy"],
                "terminal_value_usd": summary["terminal_value_usd"],
                "hodl_alpha_usd": summary["hodl_alpha_usd"],
                "fees_usd": summary["fees_usd"],
                "costs_usd": summary["costs_usd"],
                "max_drawdown_usd": summary["max_drawdown_usd"],
                "rebalances": summary["rebalances"],
                "time_in_range": summary["time_in_range"],
            }
        )
    return rows
