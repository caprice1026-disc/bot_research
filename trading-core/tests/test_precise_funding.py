from decimal import Decimal

from trading_core.market_data.models import FundingSettlement
from test_position_account import _snapshot, _limits, _costs, _tick, _plan
from trading_core.simulation.position_account import PositionAccount


def _opened():
    account = PositionAccount(_snapshot(), _limits(), _costs())
    account.advance_to(_tick(0, "100"))
    account.apply_plan(_plan("1", "1", stop="95"), executable_at_ms=0)
    return account


def test_actual_mark_price_and_timestamp_used_for_funding():
    account = _opened()
    events = account.advance_to(_tick(60_000, "101"),
        funding_settlement=FundingSettlement(30_005, Decimal("0.01"), Decimal("105")))
    assert events[0].timestamp_ms == 30_005
    assert events[0].amount == Decimal("-1.05")
    assert events[0].price == Decimal("105")


def test_stop_before_funding_at_completed_bar_boundary():
    account = _opened()
    events = account.advance_to(_tick(60_000, "100", low="94"),
        funding_settlement=FundingSettlement(60_000, Decimal("0.01"), Decimal("105")))
    assert [event.kind for event in events] == ["stop_triggered", "fill"]


def test_intrabar_stop_order_is_explicitly_ambiguous_and_adverse():
    account = _opened()
    events = account.advance_to(_tick(60_000, "100", low="94"),
        funding_settlement=FundingSettlement(30_000, Decimal("0.01"), Decimal("105")))
    assert [event.kind for event in events] == ["funding_stop_ambiguous", "funding", "stop_triggered", "fill"]
    assert account.snapshot(60_000).funding_paid == Decimal("-1.05")


def test_midnight_funding_is_not_erased_from_daily_loss():
    account = PositionAccount(_snapshot(), _limits(), _costs())
    account.advance_to(_tick(86_340_000, "100"))
    account.apply_plan(_plan("1", "1", stop="95"), executable_at_ms=86_340_000)
    account.advance_to(_tick(86_400_000, "100"),
        funding_settlement=FundingSettlement(86_400_000, Decimal("0.01"), Decimal("100")))
    state = account.snapshot(86_400_000)
    assert state.day_start_equity == Decimal("1000")
    assert state.equity == Decimal("999")
    assert state.daily_realized_pnl == Decimal("-1")


def test_ambiguous_stop_does_not_credit_uncertain_funding_receipt():
    account = _opened()
    events = account.advance_to(_tick(60_000, "100", low="94"),
        funding_settlement=FundingSettlement(30_000, Decimal("-0.01"), Decimal("105")))
    assert [event.kind for event in events] == ["funding_stop_ambiguous", "stop_triggered", "fill"]
    assert account.snapshot(60_000).funding_paid == 0
