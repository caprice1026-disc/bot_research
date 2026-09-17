"""Turn a frozen target position into one safe delta order."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN

from ..accounting.models import AccountSnapshot
from ..market_data.models import MarketSnapshot


class PositionPlanError(ValueError):
    """Raised when a target cannot be frozen without a decision-time mark."""


_FRACTIONS = frozenset(
    {Decimal("-1"), Decimal("-0.5"), Decimal("-0.25"), Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("1")}
)


def _finite_non_negative(value: Decimal, *, name: str) -> None:
    if not value.is_finite() or value < 0:
        raise PositionPlanError(f"{name} must be finite and non-negative")


def _round_absolute_down(value: Decimal, step: Decimal) -> Decimal:
    if not value.is_finite() or step <= 0 or not step.is_finite():
        raise PositionPlanError("quantity and step must be finite with a positive step")
    rounded = (abs(value) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return rounded.copy_sign(value)


@dataclass(frozen=True)
class ExecutionCosts:
    """One-sided fill costs.  Fees are charged separately from price impact."""

    fee_rate: Decimal = Decimal("0.00045")
    spread_bps: Decimal = Decimal("2")
    slippage_bps: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for name in ("fee_rate", "spread_bps", "slippage_bps"):
            _finite_non_negative(getattr(self, name), name=name)

    @property
    def adverse_bps(self) -> Decimal:
        return self.spread_bps / Decimal("2") + self.slippage_bps

    def estimated_fill_cost(self, notional: Decimal) -> Decimal:
        return abs(notional) * (self.fee_rate + self.adverse_bps / Decimal("10000"))

    def execution_price(self, mark_price: Decimal, *, signed_delta: Decimal) -> Decimal:
        if signed_delta == 0:
            return mark_price
        direction = Decimal("1") if signed_delta > 0 else Decimal("-1")
        return mark_price * (Decimal("1") + direction * self.adverse_bps / Decimal("10000"))


@dataclass(frozen=True)
class RiskLimits:
    """Immutable position-management limits owned by the experiment config."""

    exposure_anchor_usd: Decimal
    max_position_notional_usd: Decimal
    min_notional_usd: Decimal
    risk_per_position_pct: Decimal
    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal
    max_hold_ms: int

    def __post_init__(self) -> None:
        for name in (
            "exposure_anchor_usd",
            "max_position_notional_usd",
            "min_notional_usd",
            "risk_per_position_pct",
            "max_daily_loss_pct",
            "max_drawdown_pct",
        ):
            _finite_non_negative(getattr(self, name), name=name)
        if self.exposure_anchor_usd <= 0 or self.max_position_notional_usd <= 0:
            raise PositionPlanError("anchor and maximum position notional must be positive")
        if self.min_notional_usd <= 0:
            raise PositionPlanError("minimum notional must be positive")
        if self.max_hold_ms <= 0:
            raise PositionPlanError("max_hold_ms must be positive")


@dataclass(frozen=True)
class PositionTarget:
    """A parsed decision after its target quantity has been frozen once."""

    decision_id: str
    hold: bool
    target_fraction: Decimal | None
    frozen_target_quantity: Decimal | None
    stop_price: Decimal | None
    source_position_version: int

    def __post_init__(self) -> None:
        if not self.decision_id:
            raise PositionPlanError("decision_id is required")
        if self.source_position_version < 0:
            raise PositionPlanError("source_position_version must be non-negative")
        if self.hold:
            if any(value is not None for value in (self.target_fraction, self.frozen_target_quantity, self.stop_price)):
                raise PositionPlanError("hold cannot include a target or stop")
            return
        if self.target_fraction not in _FRACTIONS:
            raise PositionPlanError("target_fraction must be one of the configured discrete fractions")
        if self.frozen_target_quantity is not None and not self.frozen_target_quantity.is_finite():
            raise PositionPlanError("frozen_target_quantity must be finite")
        if self.stop_price is not None and (not self.stop_price.is_finite() or self.stop_price <= 0):
            raise PositionPlanError("stop_price must be finite and positive")


@dataclass(frozen=True)
class PlanResult:
    """A delta that a venue adapter may submit exactly once, or a safe rejection."""

    status: str
    reason: str | None
    delta_quantity: Decimal
    reduce_only: bool
    target_quantity: Decimal
    stop_price: Decimal | None
    expected_cost: Decimal
    intent_id: str


def freeze_target_quantity(
    target: PositionTarget,
    market: MarketSnapshot,
    limits: RiskLimits,
) -> PositionTarget:
    """Resolve a discrete fraction once; callers persist this returned object."""

    if target.hold or target.frozen_target_quantity is not None:
        return target
    assert target.target_fraction is not None
    quantity = _round_absolute_down(
        limits.exposure_anchor_usd * target.target_fraction / market.mark_price,
        market.quantity_step,
    )
    return replace(target, frozen_target_quantity=quantity)


def _result(
    target: PositionTarget,
    *,
    status: str,
    reason: str | None,
    current: Decimal,
    delta: Decimal = Decimal("0"),
    reduce_only: bool = False,
    stop_price: Decimal | None = None,
    expected_cost: Decimal = Decimal("0"),
) -> PlanResult:
    intended_target = target.frozen_target_quantity if target.frozen_target_quantity is not None else current
    return PlanResult(
        status=status,
        reason=reason,
        delta_quantity=delta,
        reduce_only=reduce_only,
        target_quantity=intended_target,
        stop_price=stop_price,
        expected_cost=expected_cost,
        intent_id=f"{target.decision_id}:position-v{target.source_position_version}",
    )


def _is_stop_valid(quantity: Decimal, stop_price: Decimal, mark_price: Decimal) -> bool:
    return stop_price < mark_price if quantity > 0 else stop_price > mark_price


def _is_stop_loosened(current_quantity: Decimal, old_stop: Decimal | None, new_stop: Decimal) -> bool:
    if old_stop is None or current_quantity == 0:
        return False
    return new_stop < old_stop if current_quantity > 0 else new_stop > old_stop


def _risk_limited(account: AccountSnapshot, limits: RiskLimits) -> bool:
    if account.day_start_equity <= 0 or account.peak_equity <= 0:
        return True
    daily_limit = account.day_start_equity * limits.max_daily_loss_pct / Decimal("100")
    drawdown_pct = (account.peak_equity - account.equity) / account.peak_equity * Decimal("100")
    return -account.daily_realized_pnl >= daily_limit or drawdown_pct >= limits.max_drawdown_pct


def plan_position_delta(
    target: PositionTarget,
    snapshot: AccountSnapshot,
    market: MarketSnapshot,
    limits: RiskLimits,
    *,
    costs: ExecutionCosts = ExecutionCosts(),
) -> PlanResult:
    """Validate one frozen target without silently resizing or reversing it."""

    current = snapshot.signed_quantity
    if target.hold:
        return _result(target, status="no_op", reason="hold", current=current)
    if target.source_position_version != snapshot.position_version:
        return _result(target, status="rejected", reason="stale_position", current=current)
    if target.frozen_target_quantity is None:
        return _result(target, status="rejected", reason="target_not_frozen", current=current)

    desired = target.frozen_target_quantity
    if _round_absolute_down(desired, market.quantity_step) != desired:
        return _result(target, status="rejected", reason="target_not_quantized", current=current)
    if current and desired and (current > 0) != (desired > 0):
        return _result(target, status="rejected", reason="reversal_requires_flat", current=current)

    delta = desired - current
    if delta == 0:
        return _result(target, status="no_op", reason="target_already_met", current=current)
    reduce_only = abs(desired) < abs(current)
    delta_notional = abs(delta) * market.mark_price
    if not reduce_only and delta_notional < limits.min_notional_usd:
        return _result(target, status="no_op", reason="no_op_dust", current=current)

    effective_stop = target.stop_price if target.stop_price is not None else snapshot.stop_price
    if desired != 0 and target.stop_price is not None and _is_stop_loosened(current, snapshot.stop_price, target.stop_price):
        return _result(target, status="rejected", reason="stop_loosened", current=current)
    if not reduce_only:
        if _risk_limited(snapshot, limits):
            return _result(target, status="rejected", reason="account_risk_limit", current=current)
        if effective_stop is None or not _is_stop_valid(desired, effective_stop, market.mark_price):
            return _result(target, status="rejected", reason="invalid_or_missing_stop", current=current)
        if abs(desired) * market.mark_price > limits.max_position_notional_usd:
            return _result(target, status="rejected", reason="max_position_notional", current=current)
        stop_risk = abs(desired) * abs(market.mark_price - effective_stop)
        stop_risk += costs.estimated_fill_cost(abs(desired) * market.mark_price)
        max_risk = snapshot.equity * limits.risk_per_position_pct / Decimal("100")
        if stop_risk > max_risk:
            return _result(target, status="rejected", reason="stop_risk_limit", current=current)
    elif desired != 0 and effective_stop is None:
        return _result(target, status="rejected", reason="missing_existing_stop", current=current)

    return _result(
        target,
        status="planned",
        reason=None,
        current=current,
        delta=delta,
        reduce_only=reduce_only,
        stop_price=effective_stop if desired != 0 else None,
        expected_cost=costs.estimated_fill_cost(delta_notional),
    )
