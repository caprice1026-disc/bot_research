"""Forecast and equity-curve metrics."""

from __future__ import annotations

from collections.abc import Sequence
from math import log


def _validate_binary_inputs(probabilities: Sequence[float], outcomes: Sequence[bool]) -> None:
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must be non-empty and equal length")
    if any(not 0.0 <= value <= 1.0 for value in probabilities):
        raise ValueError("probabilities must be between 0 and 1")


def brier_score(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    _validate_binary_inputs(probabilities, outcomes)
    return sum((probability - float(outcome)) ** 2 for probability, outcome in zip(probabilities, outcomes)) / len(probabilities)


def log_loss(probabilities: Sequence[float], outcomes: Sequence[bool], epsilon: float = 1e-15) -> float:
    _validate_binary_inputs(probabilities, outcomes)
    if epsilon <= 0 or epsilon >= 0.5:
        raise ValueError("epsilon must be between 0 and 0.5")
    losses = []
    for probability, outcome in zip(probabilities, outcomes):
        bounded = min(1.0 - epsilon, max(epsilon, probability))
        losses.append(-log(bounded if outcome else 1.0 - bounded))
    return sum(losses) / len(losses)


def maximum_drawdown(equity_curve: Sequence[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return drawdown
