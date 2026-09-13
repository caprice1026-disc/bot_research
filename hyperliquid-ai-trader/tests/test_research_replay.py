from decimal import Decimal

from hyperliquid_ai_trader.research.risk import ResearchRiskEngine
from hyperliquid_ai_trader.research.simulator import VirtualAccount


def test_virtual_account_resets_daily_state_at_utc_boundary_without_trade() -> None:
    account = VirtualAccount(Decimal("1000"), Decimal("1000"))
    account.daily_realized_pnl = Decimal("-10")
    account.utc_day = 0
    account.advance_time(86_400_000)
    assert account.day_start_equity == Decimal("1000")
    assert account.daily_realized_pnl == Decimal("0")


def test_risk_engine_blocks_existing_position_and_daily_loss() -> None:
    engine = ResearchRiskEngine(
        risk_per_trade_pct=Decimal("1"), max_daily_loss_pct=Decimal("2"),
        max_drawdown_pct=Decimal("25"), max_position_notional_usd=Decimal("250"),
        leverage=Decimal("5"), min_notional_usd=Decimal("10"),
    )
    account = VirtualAccount(Decimal("1000"), Decimal("1000"))
    account.daily_realized_pnl = Decimal("-20")
    assert engine.check_entry(account=account, entry_price=Decimal("100"), stop_loss_pct=Decimal("1"), position_open=True).reason == "position_open"
    assert engine.check_entry(account=account, entry_price=Decimal("100"), stop_loss_pct=Decimal("1"), position_open=False).reason == "daily_loss_limit"
