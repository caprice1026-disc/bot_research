from __future__ import annotations


def hodl_terminal_value(token0_amount: float, token1_amount: float, final_price_token1_per_token0: float) -> float:
    return token0_amount * final_price_token1_per_token0 + token1_amount


def hodl_relative_alpha(lp_terminal_value: float, hodl_value: float) -> float:
    return lp_terminal_value - hodl_value


def net_lvr_view(gross_fees_usd: float, lvr_usd: float, execution_costs_usd: float) -> float:
    return gross_fees_usd - lvr_usd - execution_costs_usd
