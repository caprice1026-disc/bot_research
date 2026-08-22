from __future__ import annotations


Q96 = 1 << 96


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def amount0_delta(sqrt_a: int, sqrt_b: int, liquidity: int, round_up: bool = False) -> int:
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    if sqrt_a <= 0 or liquidity < 0:
        raise ValueError("sqrt ratios must be positive and liquidity non-negative")
    numerator = liquidity * (sqrt_b - sqrt_a) * Q96
    denominator = sqrt_b * sqrt_a
    return _ceil_div(numerator, denominator) if round_up else numerator // denominator


def amount1_delta(sqrt_a: int, sqrt_b: int, liquidity: int, round_up: bool = False) -> int:
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    if sqrt_a <= 0 or liquidity < 0:
        raise ValueError("sqrt ratios must be positive and liquidity non-negative")
    numerator = liquidity * (sqrt_b - sqrt_a)
    return _ceil_div(numerator, Q96) if round_up else numerator // Q96


def amounts_for_liquidity(current: int, lower: int, upper: int, liquidity: int) -> tuple[int, int]:
    if not lower < upper:
        raise ValueError("lower sqrt ratio must be below upper sqrt ratio")
    if liquidity < 0:
        raise ValueError("liquidity must be non-negative")
    if current <= lower:
        return amount0_delta(lower, upper, liquidity), 0
    if current < upper:
        return amount0_delta(current, upper, liquidity), amount1_delta(lower, current, liquidity)
    return 0, amount1_delta(lower, upper, liquidity)


def _liquidity_for_amount0(amount0: int, sqrt_a: int, sqrt_b: int) -> int:
    if amount0 <= 0:
        return 0
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return amount0 * sqrt_a * sqrt_b // Q96 // (sqrt_b - sqrt_a)


def _liquidity_for_amount1(amount1: int, sqrt_a: int, sqrt_b: int) -> int:
    if amount1 <= 0:
        return 0
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    return amount1 * Q96 // (sqrt_b - sqrt_a)


def liquidity_for_amounts(current: int, lower: int, upper: int, amount0: int, amount1: int) -> int:
    if not lower < upper:
        raise ValueError("lower sqrt ratio must be below upper sqrt ratio")
    if current <= lower:
        return _liquidity_for_amount0(amount0, lower, upper)
    if current < upper:
        return min(
            _liquidity_for_amount0(amount0, current, upper),
            _liquidity_for_amount1(amount1, lower, current),
        )
    return _liquidity_for_amount1(amount1, lower, upper)
