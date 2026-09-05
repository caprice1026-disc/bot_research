"""The only model-callable trading function, guarded by deterministic risk code."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import Settings
from .exchange.base import BracketResult, TradingExchange
from .exchange.hyperliquid import make_cloids
from .models import AccountState, MarketSnapshot, Side, TradeDecision
from .risk import RiskEngine
from .storage import SQLiteStore


class ToolRejected(RuntimeError):
    """Raised before an order is sent when the tool contract is violated."""


@dataclass(frozen=True)
class ToolExecutionResult:
    success: bool
    simulated: bool
    recovered: bool
    error_type: str | None
    plan: dict[str, str]
    cloids: dict[str, str]


class TradingTools:
    def __init__(
        self,
        *,
        exchange: TradingExchange,
        risk_engine: RiskEngine,
        store: SQLiteStore,
        settings: Settings,
        run_id: str,
        slot: int,
        day_start_equity: Decimal,
        session_peak_equity: Decimal,
        daily_realized_pnl: Decimal,
        now_ms: int,
    ) -> None:
        self.exchange = exchange
        self.risk_engine = risk_engine
        self.store = store
        self.settings = settings
        self.run_id = run_id
        self.slot = slot
        self.day_start_equity = day_start_equity
        self.session_peak_equity = session_peak_equity
        self.daily_realized_pnl = daily_realized_pnl
        self.now_ms = now_ms
        self._used = False

    def open_position(
        self,
        *,
        side: str,
        stop_loss_pct: float,
        take_profit_pct: float,
        confidence: float,
        thesis: str,
        would_abstain: bool,
        abstain_reason: str | None,
    ) -> ToolExecutionResult:
        if self._used:
            raise ToolRejected("open_position was already used in this cycle")
        self._used = True
        try:
            decision = TradeDecision(
                side=Side(side.lower()),
                stop_loss_pct=Decimal(str(stop_loss_pct)),
                take_profit_pct=Decimal(str(take_profit_pct)),
                confidence=Decimal(str(confidence)),
                thesis=thesis,
                would_abstain=would_abstain,
                abstain_reason=abstain_reason,
            )
        except (ValueError, InvalidOperation, AttributeError) as exc:
            raise ToolRejected("trade decision arguments are invalid") from exc

        market = self.exchange.get_market_observation(self.settings.coin, now_ms=self.now_ms)
        exchange_account = self.exchange.get_account_snapshot(self.settings.coin)
        if exchange_account.position_size != 0 or exchange_account.open_orders:
            raise ToolRejected("account is not clean before entry")
        account = AccountState(
            equity=exchange_account.equity,
            withdrawable=exchange_account.withdrawable,
            day_start_equity=self.day_start_equity,
            daily_realized_pnl=self.daily_realized_pnl,
            session_peak_equity=self.session_peak_equity,
        )
        plan = self.risk_engine.create_plan(
            decision,
            MarketSnapshot(entry_price=market.mark, size_decimals=market.size_decimals),
            account,
        )
        plan_payload = {
            "side": plan.side.value,
            "size": str(plan.size),
            "notional": str(plan.notional),
            "entry_price": str(plan.entry_price),
            "stop_loss_price": str(plan.stop_loss_price),
            "take_profit_price": str(plan.take_profit_price),
        }

        if self.settings.execution_mode == "dry_run":
            cloids = make_cloids(self.run_id, self.slot)
            self._record_orders(cloids, [{"simulated": {}}] * 3, plan_payload)
            return ToolExecutionResult(True, True, False, None, plan_payload, cloids)

        try:
            result = self.exchange.place_bracket(
                coin=self.settings.coin,
                plan=plan,
                run_id=self.run_id,
                slot=self.slot,
                max_entry_slippage_bps=self.settings.max_entry_slippage_bps,
            )
        except Exception:
            result = BracketResult(
                success=False,
                requires_recovery=True,
                error_type="ambiguous_response",
                cloids=make_cloids(self.run_id, self.slot),
                statuses=[],
            )
        self._record_orders(result.cloids, result.statuses, plan_payload)
        if result.success:
            return ToolExecutionResult(True, False, False, None, plan_payload, result.cloids)

        recovered = False
        if result.requires_recovery:
            try:
                self.exchange.cancel_bot_orders(self.settings.coin, list(result.cloids.values()))
            except Exception:
                pass
            try:
                self.exchange.close_position(self.settings.coin)
            except Exception:
                pass
            try:
                after = self.exchange.get_account_snapshot(self.settings.coin)
                recovered = after.position_size == 0 and not after.open_orders
            except Exception:
                recovered = False
        return ToolExecutionResult(
            False,
            False,
            recovered,
            result.error_type,
            plan_payload,
            result.cloids,
        )

    def _record_orders(
        self,
        cloids: dict[str, str],
        statuses: list[dict[str, Any]],
        plan: dict[str, str],
    ) -> None:
        prices = {
            "entry": Decimal(plan["entry_price"]),
            "tp": Decimal(plan["take_profit_price"]),
            "sl": Decimal(plan["stop_loss_price"]),
        }
        for index, leg in enumerate(("entry", "tp", "sl")):
            status_payload = statuses[index] if index < len(statuses) else {}
            if "filled" in status_payload:
                status = "filled"
                oid = status_payload["filled"].get("oid")
            elif "resting" in status_payload:
                status = "resting"
                oid = status_payload["resting"].get("oid")
            elif "simulated" in status_payload:
                status = "simulated"
                oid = None
            elif status_payload == "waitingForTrigger":
                status = "waiting_for_trigger"
                oid = None
            elif status_payload == "waitingForFill":
                status = "waiting_for_fill"
                oid = None
            elif "error" in status_payload:
                status = "error"
                oid = None
            else:
                status = "unknown"
                oid = None
            self.store.record_order(
                run_id=self.run_id,
                slot=self.slot,
                leg=leg,
                cloid=cloids[leg],
                status=status,
                size=Decimal(plan["size"]),
                price=prices[leg],
                oid=oid,
                created_at_ms=self.now_ms,
            )
