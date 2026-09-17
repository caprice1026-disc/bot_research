from decimal import Decimal

from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import ExecutionCosts, PlanResult, RiskLimits
from trading_core.market_data.models import MarketTick
from trading_core.simulation.position_account import PositionAccount


def _snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        position_version=0,
        signed_quantity=Decimal("0"),
        average_entry_price=None,
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
        opened_at_ms=None,
        stop_price=None,
        pending_order_ids=(),
        day_start_equity=Decimal("1000"),
        daily_realized_pnl=Decimal("0"),
        peak_equity=Decimal("1000"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        exposure_anchor_usd=Decimal("250"),
        max_position_notional_usd=Decimal("250"),
        min_notional_usd=Decimal("10"),
        risk_per_position_pct=Decimal("10"),
        max_daily_loss_pct=Decimal("20"),
        max_drawdown_pct=Decimal("25"),
        max_hold_ms=86_400_000,
    )


def _costs() -> ExecutionCosts:
    return ExecutionCosts(fee_rate=Decimal("0"), spread_bps=Decimal("0"), slippage_bps=Decimal("0"))


def _tick(timestamp_ms: int, price: str, *, low: str | None = None, high: str | None = None) -> MarketTick:
    return MarketTick(
        timestamp_ms=timestamp_ms,
        open_price=Decimal(price),
        high_price=Decimal(high or price),
        low_price=Decimal(low or price),
        close_price=Decimal(price),
    )


def _plan(delta: str, target: str, *, stop: str | None, reduce_only: bool = False) -> PlanResult:
    return PlanResult(
        status="planned",
        reason=None,
        delta_quantity=Decimal(delta),
        reduce_only=reduce_only,
        target_quantity=Decimal(target),
        stop_price=Decimal(stop) if stop is not None else None,
        expected_cost=Decimal("0"),
        intent_id=f"intent:{target}",
    )


def test_open_hold_add_reduce_close_is_one_lifecycle_and_hold_has_no_fee() -> None:
    account = PositionAccount(_snapshot(), _limits(), _costs())
    account.advance_to(_tick(0, "50000"))
    account.apply_plan(_plan("0.002", "0.002", stop="49000"), executable_at_ms=0)

    held = account.advance_to(_tick(300_000, "51000"))
    assert held == ()
    assert account.snapshot(300_000).signed_quantity == Decimal("0.002")
    assert account.snapshot(300_000).fees_paid == Decimal("0")

    account.advance_to(_tick(600_000, "52000"))
    account.apply_plan(_plan("0.001", "0.003", stop="50000"), executable_at_ms=600_000)
    after_add = account.snapshot(600_000)
    assert after_add.average_entry_price == Decimal("50666.66666666666666666666667")
    assert after_add.opened_at_ms == 0

    account.advance_to(_tick(900_000, "51000"))
    account.apply_plan(_plan("-0.001", "0.002", stop="50000", reduce_only=True), executable_at_ms=900_000)
    after_reduce = account.snapshot(900_000)
    assert after_reduce.realized_pnl == Decimal("0.33333333333333333333333333")
    assert after_reduce.average_entry_price == Decimal("50666.66666666666666666666667")

    account.advance_to(_tick(1_200_000, "51000"))
    events = account.apply_plan(_plan("-0.002", "0", stop=None, reduce_only=True), executable_at_ms=1_200_000)
    assert len([event for event in events if event.kind == "fill"]) == 1
    assert account.snapshot(1_200_000).signed_quantity == Decimal("0")


def test_stop_uses_an_adverse_gap_price_and_clears_the_position() -> None:
    account = PositionAccount(_snapshot(), _limits(), _costs())
    account.advance_to(_tick(0, "100"))
    account.apply_plan(_plan("1", "1", stop="95"), executable_at_ms=0)

    events = account.advance_to(_tick(60_000, "90", low="89", high="91"))

    assert [event.kind for event in events] == ["stop_triggered", "fill"]
    assert events[1].price == Decimal("90")
    assert account.snapshot(60_000).signed_quantity == Decimal("0")


def test_funding_applies_only_to_a_position_held_before_the_boundary() -> None:
    account = PositionAccount(_snapshot(), _limits(), _costs())
    account.advance_to(_tick(0, "100"))
    account.apply_plan(_plan("2", "2", stop="90"), executable_at_ms=0)

    events = account.advance_to(_tick(28_800_000, "100"), funding_rate=Decimal("0.01"))

    assert [event.kind for event in events] == ["funding"]
    assert events[0].amount == Decimal("-2.00")
    assert account.snapshot(28_800_000).funding_paid == Decimal("-2.00")


def test_terminal_snapshot_does_not_imagine_a_close() -> None:
    account = PositionAccount(_snapshot(), _limits(), _costs())
    account.advance_to(_tick(0, "100"))
    account.apply_plan(_plan("1", "1", stop="90"), executable_at_ms=0)
    account.advance_to(_tick(60_000, "101"))

    terminal = account.snapshot(60_000)

    assert terminal.signed_quantity == Decimal("1")
    assert terminal.unrealized_pnl == Decimal("1")


def test_addition_does_not_extend_the_emergency_holding_deadline() -> None:
    limits = RiskLimits(
        exposure_anchor_usd=Decimal("250"),
        max_position_notional_usd=Decimal("250"),
        min_notional_usd=Decimal("10"),
        risk_per_position_pct=Decimal("10"),
        max_daily_loss_pct=Decimal("20"),
        max_drawdown_pct=Decimal("25"),
        max_hold_ms=60_000,
    )
    account = PositionAccount(_snapshot(), limits, _costs())
    account.advance_to(_tick(0, "100"))
    account.apply_plan(_plan("1", "1", stop="90"), executable_at_ms=0)
    account.advance_to(_tick(30_000, "100"))
    account.apply_plan(_plan("1", "2", stop="90"), executable_at_ms=30_000)

    events = account.advance_to(_tick(60_000, "100"))

    assert events[0].reason == "max_hold"
    assert account.snapshot(60_000).signed_quantity == Decimal("0")


def test_a_fill_charges_each_execution_cost_once() -> None:
    costs = ExecutionCosts(fee_rate=Decimal("0.001"), spread_bps=Decimal("0"), slippage_bps=Decimal("0"))
    account = PositionAccount(_snapshot(), _limits(), costs)
    account.advance_to(_tick(0, "100"))
    account.apply_plan(_plan("1", "1", stop="90"), executable_at_ms=0)
    account.advance_to(_tick(60_000, "100"))
    account.apply_plan(_plan("-1", "0", stop=None, reduce_only=True), executable_at_ms=60_000)

    snapshot = account.snapshot(60_000)
    assert snapshot.fees_paid == Decimal("0.2")
    assert snapshot.equity == Decimal("999.8")


def test_utc_day_boundary_resets_only_the_daily_realized_counter() -> None:
    account = PositionAccount(_snapshot(), _limits(), _costs())
    account.advance_to(_tick(0, "100"))
    account.apply_plan(_plan("1", "1", stop="90"), executable_at_ms=0)
    account.advance_to(_tick(60_000, "101"))
    account.apply_plan(_plan("-1", "0", stop=None, reduce_only=True), executable_at_ms=60_000)

    account.advance_to(_tick(86_400_000, "101"))
    snapshot = account.snapshot(86_400_000)

    assert snapshot.daily_realized_pnl == Decimal("0")
    assert snapshot.day_start_equity == snapshot.equity
