from __future__ import annotations

from dataclasses import dataclass

from base_lp.backtest.cost_model import BlockFee, CostModel
from base_lp.backtest.models import BacktestResult, EquityPoint, Observation
from base_lp.schemas import SwapEvent
from base_lp.uniswap_v3.liquidity_math import amounts_for_liquidity, liquidity_for_amounts
from base_lp.uniswap_v3.tick_math import get_sqrt_ratio_at_tick


@dataclass(frozen=True)
class SimulationConfig:
    capital_usd: float
    token0_decimals: int
    token1_decimals: int
    fee_tier: int
    gas_used: int = 200_000
    gas_price_wei: int = 1_000_000
    l1_fee_wei: int = 0
    eth_price_usd: float = 2_000.0
    inventory_swap_usd: float | None = None
    safety_margin_usd: float = 0.0


def price_token1_per_token0(sqrt_price_x96: int, token0_decimals: int, token1_decimals: int) -> float:
    raw_price = (sqrt_price_x96 * sqrt_price_x96) / float(1 << 192)
    return raw_price * 10 ** (token0_decimals - token1_decimals)


def _human_amount(raw_amount: int, decimals: int) -> float:
    return raw_amount / 10**decimals


def run_backtest(events, strategy, config: SimulationConfig, cost_model: CostModel | None = None) -> BacktestResult:
    events = list(events)
    if not events:
        raise ValueError("backtest requires at least one SwapEvent")
    cost_model = cost_model or CostModel(fee_tier=config.fee_tier)
    first = events[0]
    first_price = price_token1_per_token0(first.sqrt_price_x96, config.token0_decimals, config.token1_decimals)
    token0_raw = int(config.capital_usd / 2 / first_price * 10**config.token0_decimals)
    token1_raw = int(config.capital_usd / 2 * 10**config.token1_decimals)
    lower_tick, upper_tick = strategy.initial_range(first.tick)
    lower_sqrt = get_sqrt_ratio_at_tick(lower_tick)
    upper_sqrt = get_sqrt_ratio_at_tick(upper_tick)
    position_liquidity = liquidity_for_amounts(
        first.sqrt_price_x96,
        lower_sqrt,
        upper_sqrt,
        token0_raw,
        token1_raw,
    )
    if position_liquidity <= 0:
        raise ValueError("initial capital does not create positive liquidity")
    last_reset_timestamp = first.timestamp
    total_fees = 0.0
    total_costs = 0.0
    equity_curve: list[EquityPoint] = []
    action_log: list[dict[str, object]] = []
    for event in events:
        price = price_token1_per_token0(event.sqrt_price_x96, config.token0_decimals, config.token1_decimals)
        lower_sqrt = get_sqrt_ratio_at_tick(lower_tick)
        upper_sqrt = get_sqrt_ratio_at_tick(upper_tick)
        in_range = lower_tick <= event.tick < upper_tick
        amount0_raw, amount1_raw = amounts_for_liquidity(
            event.sqrt_price_x96,
            lower_sqrt,
            upper_sqrt,
            position_liquidity,
        )
        position_value = _human_amount(amount0_raw, config.token0_decimals) * price + _human_amount(
            amount1_raw, config.token1_decimals
        )
        input_raw: int
        input_decimals: int
        if event.amount0 > 0:
            input_raw, input_decimals = event.amount0, config.token0_decimals
            input_price = price
        elif event.amount1 > 0:
            input_raw, input_decimals = event.amount1, config.token1_decimals
            input_price = 1.0
        else:
            input_raw, input_decimals, input_price = 0, config.token1_decimals, 1.0
        active_share = min(1.0, position_liquidity / max(event.liquidity, 1)) if in_range else 0.0
        fee_raw = input_raw * config.fee_tier / 1_000_000 * active_share
        fee_usd = _human_amount(int(fee_raw), input_decimals) * input_price
        if fee_raw > 0 and fee_usd == 0:
            fee_usd = fee_raw / 10**input_decimals * input_price
        total_fees += fee_usd
        inventory_swap_usd = config.inventory_swap_usd
        if inventory_swap_usd is None:
            inventory_swap_usd = config.capital_usd * 0.5
        estimated_cost = cost_model.estimate(
            "reset",
            BlockFee(config.gas_price_wei, config.gas_used, config.l1_fee_wei, config.eth_price_usd),
            price,
            inventory_swap_usd,
        )
        observation = Observation(
            block_number=event.block_number,
            timestamp=event.timestamp,
            tick=event.tick,
            sqrt_price_x96=event.sqrt_price_x96,
            pool_liquidity=event.liquidity,
            current_lower_tick=lower_tick,
            current_upper_tick=upper_tick,
            last_reset_timestamp=last_reset_timestamp,
            estimated_rebalance_cost_usd=estimated_cost.total_usd,
            capital_usd=config.capital_usd,
        )
        decision = strategy.decide(observation)
        action_log.append(
            {
                "block_number": event.block_number,
                "timestamp": event.timestamp,
                "action": decision.action,
                "reason": decision.reason,
                "expected_benefit_usd": decision.expected_benefit_usd,
            }
        )
        if decision.action == "reset":
            total_costs += estimated_cost.total_usd
            lower_tick, upper_tick = decision.target_lower_tick, decision.target_upper_tick
            lower_sqrt = get_sqrt_ratio_at_tick(lower_tick)
            upper_sqrt = get_sqrt_ratio_at_tick(upper_tick)
            current_value = position_value + total_fees - total_costs
            token0_raw = int(max(current_value, 0.0) / 2 / price * 10**config.token0_decimals)
            token1_raw = int(max(current_value, 0.0) / 2 * 10**config.token1_decimals)
            position_liquidity = liquidity_for_amounts(
                event.sqrt_price_x96,
                lower_sqrt,
                upper_sqrt,
                token0_raw,
                token1_raw,
            )
            last_reset_timestamp = event.timestamp
            position_value = _human_amount(
                amounts_for_liquidity(event.sqrt_price_x96, lower_sqrt, upper_sqrt, position_liquidity)[0],
                config.token0_decimals,
            ) * price + _human_amount(
                amounts_for_liquidity(event.sqrt_price_x96, lower_sqrt, upper_sqrt, position_liquidity)[1],
                config.token1_decimals,
            )
        equity_curve.append(
            EquityPoint(
                block_number=event.block_number,
                timestamp=event.timestamp,
                value_usd=position_value + total_fees - total_costs,
                fees_usd=total_fees,
                costs_usd=total_costs,
                in_range=in_range,
            )
        )
    return BacktestResult(
        strategy_name=getattr(strategy, "name", strategy.__class__.__name__),
        equity_curve=equity_curve,
        action_log=action_log,
        total_fees_usd=total_fees,
        total_costs_usd=total_costs,
        terminal_value_usd=equity_curve[-1].value_usd,
    )
