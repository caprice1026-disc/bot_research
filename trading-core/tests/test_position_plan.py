from decimal import Decimal

import pytest

from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import (
    ExecutionCosts,
    PositionTarget,
    RiskLimits,
    freeze_target_quantity,
    plan_position_delta,
)
from trading_core.market_data.models import MarketSnapshot


def _account(*, quantity: str = "0", stop: str | None = None) -> AccountSnapshot:
    return AccountSnapshot(
        position_version=4,
        signed_quantity=Decimal(quantity),
        average_entry_price=Decimal("50000") if quantity != "0" else None,
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
        opened_at_ms=0 if quantity != "0" else None,
        stop_price=Decimal(stop) if stop else None,
        pending_order_ids=(),
        day_start_equity=Decimal("1000"),
        daily_realized_pnl=Decimal("0"),
        peak_equity=Decimal("1000"),
    )


def _market(price: str = "50000") -> MarketSnapshot:
    return MarketSnapshot(
        as_of_ms=600_000,
        available_at_ms=600_000,
        mark_price=Decimal(price),
        quantity_step=Decimal("0.0001"),
    )


def _limits(**overrides: object) -> RiskLimits:
    values: dict[str, object] = {
        "exposure_anchor_usd": Decimal("250"),
        "max_position_notional_usd": Decimal("250"),
        "min_notional_usd": Decimal("10"),
        "risk_per_position_pct": Decimal("1"),
        "max_daily_loss_pct": Decimal("20"),
        "max_drawdown_pct": Decimal("25"),
        "max_hold_ms": 86_400_000,
    }
    values.update(overrides)
    return RiskLimits(**values)


def _target(
    *,
    fraction: str | None,
    stop: str | None,
    hold: bool = False,
    version: int = 4,
) -> PositionTarget:
    return PositionTarget(
        decision_id="fixture:600000",
        hold=hold,
        target_fraction=Decimal(fraction) if fraction is not None else None,
        frozen_target_quantity=None,
        stop_price=Decimal(stop) if stop is not None else None,
        source_position_version=version,
    )


def test_target_is_frozen_once_and_uses_only_delta_quantity() -> None:
    market = _market()
    target = freeze_target_quantity(_target(fraction="0.5", stop="49000"), market, _limits())

    assert target.frozen_target_quantity == Decimal("0.0025")

    plan = plan_position_delta(target, _account(quantity="0.001"), market, _limits())

    assert plan.status == "planned"
    assert plan.target_quantity == Decimal("0.0025")
    assert plan.delta_quantity == Decimal("0.0015")
    assert plan.reduce_only is False


def test_hold_never_rebalances_when_price_changes() -> None:
    hold = _target(fraction=None, stop=None, hold=True)

    plan = plan_position_delta(hold, _account(quantity="0.001"), _market("90000"), _limits())

    assert plan.status == "no_op"
    assert plan.reason == "hold"
    assert plan.delta_quantity == Decimal("0")


def test_target_zero_is_a_reduce_only_full_close() -> None:
    target = freeze_target_quantity(_target(fraction="0", stop=None), _market(), _limits())

    plan = plan_position_delta(target, _account(quantity="0.001"), _market(), _limits())

    assert plan.status == "planned"
    assert plan.delta_quantity == Decimal("-0.001")
    assert plan.target_quantity == Decimal("0")
    assert plan.reduce_only is True


def test_reversal_is_rejected_until_the_existing_position_is_flat() -> None:
    target = freeze_target_quantity(_target(fraction="-0.5", stop="51000"), _market(), _limits())

    plan = plan_position_delta(target, _account(quantity="0.001", stop="49000"), _market(), _limits())

    assert plan.status == "rejected"
    assert plan.reason == "reversal_requires_flat"


def test_tiny_increment_is_not_sent_as_churn() -> None:
    target = PositionTarget(
        decision_id="fixture:dust",
        hold=False,
        target_fraction=Decimal("0.5"),
        frozen_target_quantity=Decimal("0.0011"),
        stop_price=Decimal("49000"),
        source_position_version=4,
    )

    plan = plan_position_delta(target, _account(quantity="0.001"), _market(), _limits())

    assert plan.status == "no_op"
    assert plan.reason == "no_op_dust"


def test_addition_rejects_a_stop_risk_above_the_fixed_limit() -> None:
    target = freeze_target_quantity(_target(fraction="1", stop="45000"), _market(), _limits())

    plan = plan_position_delta(target, _account(quantity="0.0025"), _market(), _limits())

    assert plan.status == "rejected"
    assert plan.reason == "stop_risk_limit"


def test_existing_long_stop_cannot_be_loosened_on_an_addition() -> None:
    target = freeze_target_quantity(_target(fraction="1", stop="48000"), _market(), _limits())

    plan = plan_position_delta(target, _account(quantity="0.0025", stop="49000"), _market(), _limits())

    assert plan.status == "rejected"
    assert plan.reason == "stop_loosened"


def test_execution_costs_are_included_once_in_a_planned_delta() -> None:
    target = freeze_target_quantity(_target(fraction="0.5", stop="49000"), _market(), _limits())
    costs = ExecutionCosts(fee_rate=Decimal("0.001"), spread_bps=Decimal("2"), slippage_bps=Decimal("1"))

    plan = plan_position_delta(target, _account(), _market(), _limits(), costs=costs)

    assert plan.expected_cost == Decimal("0.1500")


@pytest.mark.parametrize("quantity,fraction,old_stop,new_stop", [
    ("0.005", "0.5", "49000", "51000"),
    ("0.005", "0.5", "49000", "50000"),
    ("-0.005", "-0.5", "51000", "49000"),
    ("-0.005", "-0.5", "51000", "50000"),
])
def test_partial_reduction_rejects_stop_at_or_across_market(quantity, fraction, old_stop, new_stop):
    target = freeze_target_quantity(_target(fraction=fraction, stop=new_stop), _market(), _limits())
    plan = plan_position_delta(target, _account(quantity=quantity, stop=old_stop), _market(), _limits())
    assert plan.status == "rejected"
    assert plan.reason == "invalid_or_missing_stop"


@pytest.mark.parametrize("quantity,fraction,old_stop,new_stop", [
    ("0.005", "0.5", "49000", "49500"),
    ("-0.005", "-0.5", "51000", "50500"),
])
def test_partial_reduction_accepts_valid_tighter_stop(quantity, fraction, old_stop, new_stop):
    target = freeze_target_quantity(_target(fraction=fraction, stop=new_stop), _market(), _limits())
    plan = plan_position_delta(target, _account(quantity=quantity, stop=old_stop), _market(), _limits())
    assert plan.status == "planned"
    assert plan.reduce_only
    assert plan.stop_price == Decimal(new_stop)
