"""Stateful one-position simulator for target-position experiments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..accounting.models import AccountSnapshot
from ..execution.position_plan import ExecutionCosts, PlanResult, RiskLimits
from ..market_data.models import MarketTick


class PositionAccountError(ValueError):
    """Raised when an event would make the simulated account time-inconsistent."""


@dataclass(frozen=True)
class AccountEvent:
    kind: str
    timestamp_ms: int
    quantity: Decimal = Decimal("0")
    price: Decimal | None = None
    amount: Decimal = Decimal("0")
    reason: str | None = None


class PositionAccount:
    """Applies fills once and retains a position until a plan or protection closes it."""

    def __init__(self, initial: AccountSnapshot, limits: RiskLimits, costs: ExecutionCosts) -> None:
        self._limits = limits
        self._costs = costs
        self._position_version = initial.position_version
        self._quantity = initial.signed_quantity
        self._average_entry_price = initial.average_entry_price
        self._cash = initial.cash
        self._realized_pnl = initial.realized_pnl
        self._fees_paid = initial.fees_paid
        self._funding_paid = initial.funding_paid
        self._daily_realized_pnl = initial.daily_realized_pnl
        self._day_start_equity = initial.day_start_equity
        self._peak_equity = initial.peak_equity
        self._opened_at_ms = initial.opened_at_ms
        self._stop_price = initial.stop_price
        self._last_tick: MarketTick | None = None
        self._utc_day: int | None = None

    def _unrealized(self, mark: Decimal) -> Decimal:
        if self._quantity == 0 or self._average_entry_price is None:
            return Decimal("0")
        direction = Decimal("1") if self._quantity > 0 else Decimal("-1")
        return (mark - self._average_entry_price) * abs(self._quantity) * direction

    def _equity(self, mark: Decimal) -> Decimal:
        return self._cash + self._unrealized(mark)

    def _refresh_peak(self, mark: Decimal) -> None:
        self._peak_equity = max(self._peak_equity, self._equity(mark))

    def _advance_utc_day(self, timestamp_ms: int, mark: Decimal) -> None:
        day = timestamp_ms // 86_400_000
        if self._utc_day is None:
            self._utc_day = day
        elif day != self._utc_day:
            self._utc_day = day
            self._day_start_equity = self._equity(mark)
            self._daily_realized_pnl = Decimal("0")

    def _fill(self, delta: Decimal, raw_price: Decimal, timestamp_ms: int, *, reason: str | None) -> AccountEvent:
        if delta == 0:
            raise PositionAccountError("a fill requires a non-zero delta")
        if self._quantity and delta and (self._quantity > 0) != (delta > 0) and abs(delta) > abs(self._quantity):
            raise PositionAccountError("a fill cannot reverse a position directly")
        execution_price = self._costs.execution_price(raw_price, signed_delta=delta)
        fee = abs(delta) * execution_price * self._costs.fee_rate
        current = self._quantity
        if current == 0 or (current > 0) == (delta > 0):
            if current == 0:
                self._average_entry_price = execution_price
                self._opened_at_ms = timestamp_ms
            else:
                assert self._average_entry_price is not None
                self._average_entry_price = (
                    abs(current) * self._average_entry_price + abs(delta) * execution_price
                ) / (abs(current) + abs(delta))
            self._quantity = current + delta
        else:
            assert self._average_entry_price is not None
            closing = abs(delta)
            direction = Decimal("1") if current > 0 else Decimal("-1")
            realized = (execution_price - self._average_entry_price) * closing * direction
            self._cash += realized
            self._realized_pnl += realized
            self._daily_realized_pnl += realized
            self._quantity = current + delta
            if self._quantity == 0:
                self._average_entry_price = None
                self._opened_at_ms = None
                self._stop_price = None
        self._cash -= fee
        self._fees_paid += fee
        self._position_version += 1
        self._refresh_peak(raw_price)
        return AccountEvent("fill", timestamp_ms, quantity=delta, price=execution_price, amount=-fee, reason=reason)

    def advance_to(self, tick: MarketTick, *, funding_rate: Decimal | None = None) -> tuple[AccountEvent, ...]:
        """Process stop, emergency holding cap, funding, and then the new mark."""

        if self._last_tick is not None and tick.timestamp_ms < self._last_tick.timestamp_ms:
            raise PositionAccountError("market ticks must be time ordered")
        events: list[AccountEvent] = []
        if self._quantity and self._stop_price is not None:
            stop_hit = tick.low_price <= self._stop_price if self._quantity > 0 else tick.high_price >= self._stop_price
            if stop_hit:
                raw_exit = min(tick.open_price, self._stop_price) if self._quantity > 0 else max(tick.open_price, self._stop_price)
                events.append(AccountEvent("stop_triggered", tick.timestamp_ms, price=raw_exit, reason="stop_loss"))
                events.append(self._fill(-self._quantity, raw_exit, tick.timestamp_ms, reason="stop_loss"))
        if self._quantity and self._opened_at_ms is not None and tick.timestamp_ms - self._opened_at_ms >= self._limits.max_hold_ms:
            events.append(self._fill(-self._quantity, tick.open_price, tick.timestamp_ms, reason="max_hold"))
        if funding_rate is not None:
            if not funding_rate.is_finite():
                raise PositionAccountError("funding_rate must be finite")
            if self._quantity and self._opened_at_ms is not None and self._opened_at_ms < tick.timestamp_ms:
                payment = -self._quantity * tick.open_price * funding_rate
                self._cash += payment
                self._funding_paid += payment
                self._daily_realized_pnl += payment
                events.append(AccountEvent("funding", tick.timestamp_ms, quantity=self._quantity, price=tick.open_price, amount=payment))
        self._last_tick = tick
        self._advance_utc_day(tick.timestamp_ms, tick.close_price)
        self._refresh_peak(tick.close_price)
        return tuple(events)

    def restore_tick(self, tick: MarketTick) -> None:
        """Restore the already-accounted market boundary when resuming from SQLite."""

        if self._last_tick is not None:
            raise PositionAccountError("a market tick is already present")
        self._last_tick = tick
        self._utc_day = tick.timestamp_ms // 86_400_000

    def apply_plan(self, plan: PlanResult, *, executable_at_ms: int) -> tuple[AccountEvent, ...]:
        """Fill an already-validated delta at the current decision-time mark."""

        if plan.status != "planned":
            return ()
        if self._last_tick is None or executable_at_ms != self._last_tick.timestamp_ms:
            raise PositionAccountError("plans execute only at the latest market tick")
        if self._quantity + plan.delta_quantity != plan.target_quantity:
            raise PositionAccountError("plan delta does not match current simulated quantity")
        if plan.reduce_only and abs(plan.target_quantity) > abs(self._quantity):
            raise PositionAccountError("reduce-only plan increases exposure")
        event = self._fill(plan.delta_quantity, self._last_tick.close_price, executable_at_ms, reason="target_delta")
        self._stop_price = plan.stop_price if self._quantity else None
        return (event,)

    def snapshot(self, timestamp_ms: int) -> AccountSnapshot:
        """Return the current state without forcing an end-of-run close."""

        if self._last_tick is None:
            raise PositionAccountError("no market tick has been observed")
        if timestamp_ms < self._last_tick.timestamp_ms:
            raise PositionAccountError("snapshot cannot precede the latest tick")
        mark = self._last_tick.close_price
        return AccountSnapshot(
            position_version=self._position_version,
            signed_quantity=self._quantity,
            average_entry_price=self._average_entry_price,
            cash=self._cash,
            equity=self._equity(mark),
            unrealized_pnl=self._unrealized(mark),
            opened_at_ms=self._opened_at_ms,
            stop_price=self._stop_price,
            pending_order_ids=(),
            day_start_equity=self._day_start_equity,
            daily_realized_pnl=self._daily_realized_pnl,
            peak_equity=self._peak_equity,
            realized_pnl=self._realized_pnl,
            fees_paid=self._fees_paid,
            funding_paid=self._funding_paid,
        )
