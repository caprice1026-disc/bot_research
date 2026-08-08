"""Execution-cost and edge calculations."""

from __future__ import annotations

from math import isfinite


def _validate_probability(value: float, name: str) -> None:
    if not isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def _validate_cost(value: float, name: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be non-negative")


def executable_yes_price(observed_ask: float, spread: float, slippage: float) -> float:
    _validate_probability(observed_ask, "observed_ask")
    _validate_cost(spread, "spread")
    _validate_cost(slippage, "slippage")
    return min(1.0, observed_ask + spread + slippage)


def net_edge(
    model_probability: float,
    observed_ask: float,
    fee: float,
    spread: float,
    slippage: float,
    uncertainty_buffer: float,
) -> float:
    _validate_probability(model_probability, "model_probability")
    _validate_cost(fee, "fee")
    _validate_cost(uncertainty_buffer, "uncertainty_buffer")
    return model_probability - executable_yes_price(observed_ask, spread, slippage) - fee - uncertainty_buffer
