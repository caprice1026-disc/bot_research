from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    block_number: int
    timestamp: int
    tick: int
    sqrt_price_x96: int
    pool_liquidity: int
    current_lower_tick: int
    current_upper_tick: int
    last_reset_timestamp: int
    estimated_rebalance_cost_usd: float
    capital_usd: float


@dataclass(frozen=True)
class Decision:
    action: str
    target_lower_tick: int
    target_upper_tick: int
    reason: str
    expected_benefit_usd: float = 0.0


@dataclass(frozen=True)
class EquityPoint:
    block_number: int
    timestamp: int
    value_usd: float
    fees_usd: float
    costs_usd: float
    in_range: bool


@dataclass(frozen=True)
class BacktestResult:
    strategy_name: str
    equity_curve: list[EquityPoint]
    action_log: list[dict[str, object]]
    total_fees_usd: float
    total_costs_usd: float
    terminal_value_usd: float
