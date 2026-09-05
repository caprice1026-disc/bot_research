"""One-cycle service and drift-free local scheduler."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import time
from typing import Any, Callable

from .agents import AgentDecisionError, ReviewerAgent, TraderAgent
from .config import Settings
from .exchange.base import TradingExchange
from .features import build_market_features, estimate_mfe_mae_pct
from .risk import RiskEngine, RiskRejected
from .storage import SQLiteStore
from .strategy import initial_strategy
from .trading_tools import ToolRejected, TradingTools


@dataclass(frozen=True)
class CycleResult:
    slot: int
    status: str
    error_type: str | None = None


@dataclass(frozen=True)
class RunSummary:
    cycles: int
    reviews: int
    cleanup_ok: bool


class TradingService:
    def __init__(
        self,
        *,
        settings: Settings,
        exchange: TradingExchange,
        trader: TraderAgent,
        reviewer: ReviewerAgent | None,
        store: SQLiteStore,
        run_id: str,
        git_sha: str,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.exchange = exchange
        self.trader = trader
        self.reviewer = reviewer
        self.store = store
        self.run_id = run_id
        self.git_sha = git_sha
        self.sleeper = sleeper
        self.day_start_equity = Decimal("0")
        self.session_peak_equity = Decimal("0")
        self.daily_realized_pnl = Decimal("0")
        self.started_at_ms = 0
        self.last_slot = 0
        self.execution_halted = False
        self.execution_halt_reason: str | None = None

    def initialize(self, *, now_ms: int) -> None:
        market = self.exchange.get_market_observation(self.settings.coin, now_ms=now_ms)
        account = self.exchange.get_account_snapshot(self.settings.coin)
        if account.position_size != 0 or account.open_orders or account.unknown_exposure:
            raise RuntimeError("preflight requires a clean account")
        self.day_start_equity = account.equity
        self.session_peak_equity = account.equity
        self.started_at_ms = now_ms
        self.store.create_run(
            run_id=self.run_id,
            mode=self.settings.execution_mode,
            started_at_ms=now_ms,
            initial_equity=account.equity,
            initial_mark=market.mark,
            git_sha=self.git_sha,
        )
        if self.store.load_latest_strategy(self.run_id) is None:
            state = initial_strategy()
            self.store.save_strategy_version(
                run_id=self.run_id,
                version=1,
                parent_version=None,
                state=state,
                patch=None,
                model="bootstrap",
                created_at_ms=now_ms,
            )

    def run_once(self, *, slot: int, scheduled_at_ms: int) -> CycleResult:
        self.last_slot = slot
        strategy = self.store.load_latest_strategy(self.run_id) or initial_strategy()
        if not self.store.reserve_cycle(
            self.run_id,
            slot,
            scheduled_at_ms=scheduled_at_ms,
            strategy_version=int(strategy["version"]),
        ):
            return CycleResult(slot, "duplicate")

        if not self.cleanup():
            return self._fail_cycle(slot, "cleanup_failed", "cleanup_failed")
        if self.execution_halted:
            return self._fail_cycle(
                slot,
                "execution_halted",
                self.execution_halt_reason or "prior_execution_failure",
            )

        try:
            self.sync_exchange_evidence(now_ms=scheduled_at_ms)
            observation = self.exchange.get_market_observation(
                self.settings.coin,
                now_ms=scheduled_at_ms,
            )
            if slot > 0:
                self._record_episode_excursion(
                    slot=slot - 1,
                    observation=observation,
                    end_ms=scheduled_at_ms,
                )
            features = build_market_features(
                candles=observation.candles,
                bids=observation.bids,
                asks=observation.asks,
                mark=float(observation.mark),
                oracle=float(observation.oracle),
                funding=float(observation.funding),
                open_interest=float(observation.open_interest),
            )
            account = self.exchange.get_account_snapshot(self.settings.coin)
        except Exception:
            return self._fail_cycle(
                slot,
                "mandatory_entry_exception",
                "market_data_error",
            )
        self.session_peak_equity = max(self.session_peak_equity, account.equity)
        self.store.record_equity(
            run_id=self.run_id,
            timestamp_ms=scheduled_at_ms,
            equity=account.equity,
            withdrawable=account.withdrawable,
            mark=observation.mark,
        )
        context = {
            "market": features.to_prompt_dict(),
            "account": {
                "equity": str(account.equity),
                "withdrawable": str(account.withdrawable),
                "position_size": str(account.position_size),
                "daily_realized_pnl": str(self.daily_realized_pnl),
            },
            "strategy": strategy,
            "recent_closed_trades": self.store.recent_closed_trades(self.run_id, limit=12),
            "risk_limits": {
                "stop_loss_pct": {
                    "unit": "percentage points; 0.30 means 0.30%",
                    "minimum": str(self.settings.min_stop_loss_pct),
                    "maximum": str(self.settings.max_stop_loss_pct),
                },
                "take_profit_pct": {
                    "unit": "percentage points; 0.60 means 0.60%",
                    "minimum": str(self.settings.min_take_profit_pct),
                    "maximum": str(self.settings.max_take_profit_pct),
                },
            },
        }
        decision_payload: dict[str, Any] | None = None
        envelope = None
        try:
            envelope = self.trader.decide(context)
            decision = envelope.decision
            decision_payload = {
                **asdict(decision),
                "side": decision.side.value,
                "stop_loss_pct": str(decision.stop_loss_pct),
                "take_profit_pct": str(decision.take_profit_pct),
                "confidence": str(decision.confidence),
            }
            self.store.record_decision(
                run_id=self.run_id,
                slot=slot,
                arguments=decision_payload,
                prompt_hash=envelope.prompt_hash,
                model=envelope.model,
                temperature=float(getattr(self.trader, "temperature", 0.0)),
                created_at_ms=scheduled_at_ms,
            )
            tools = TradingTools(
                exchange=self.exchange,
                risk_engine=RiskEngine(self.settings),
                store=self.store,
                settings=self.settings,
                run_id=self.run_id,
                slot=slot,
                day_start_equity=self.day_start_equity,
                session_peak_equity=self.session_peak_equity,
                daily_realized_pnl=self.daily_realized_pnl,
                now_ms=scheduled_at_ms,
            )
            execution = tools.open_position(
                side=decision.side.value,
                stop_loss_pct=float(decision.stop_loss_pct),
                take_profit_pct=float(decision.take_profit_pct),
                confidence=float(decision.confidence),
                thesis=decision.thesis,
                would_abstain=decision.would_abstain,
                abstain_reason=decision.abstain_reason,
            )
            status = "simulated" if execution.simulated else "ordered" if execution.success else "execution_failed"
            decision_payload["plan"] = execution.plan
            self.store.complete_cycle(
                run_id=self.run_id,
                slot=slot,
                status=status,
                features=features.to_prompt_dict(),
                decision=decision_payload,
                prompt_hash=envelope.prompt_hash,
                model=envelope.model,
                error_type=execution.error_type,
                completed_at_ms=scheduled_at_ms,
            )
            if not execution.success and execution.error_type in {
                "protection_rejected",
                "partial_fill",
                "ambiguous_response",
                "exchange_rejected",
            }:
                self.execution_halted = True
                self.execution_halt_reason = execution.error_type
            return CycleResult(slot, status, execution.error_type)
        except AgentDecisionError as exc:
            return self._fail_cycle(slot, "mandatory_entry_exception", exc.error_type, features.to_prompt_dict())
        except RiskRejected:
            return self._fail_cycle(
                slot,
                "mandatory_entry_exception",
                "risk_rejected",
                features.to_prompt_dict(),
                decision=decision_payload,
                prompt_hash=envelope.prompt_hash if envelope is not None else None,
                model=envelope.model if envelope is not None else None,
            )
        except ToolRejected:
            return self._fail_cycle(slot, "mandatory_entry_exception", "tool_rejected", features.to_prompt_dict())

    def _fail_cycle(
        self,
        slot: int,
        status: str,
        error_type: str,
        features: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        prompt_hash: str | None = None,
        model: str | None = None,
    ) -> CycleResult:
        self.store.complete_cycle(
            run_id=self.run_id,
            slot=slot,
            status=status,
            features=features or {},
            decision=decision,
            prompt_hash=prompt_hash,
            model=model or getattr(self.trader, "model", None),
            error_type=error_type,
        )
        return CycleResult(slot, status, error_type)

    def sync_exchange_evidence(self, *, now_ms: int) -> None:
        get_fills = getattr(self.exchange, "get_user_fills", None)
        get_funding = getattr(self.exchange, "get_user_funding", None)
        if callable(get_fills):
            for fill in get_fills(self.started_at_ms, now_ms):
                if fill.get("coin") != self.settings.coin:
                    continue
                oid = int(fill.get("oid", 0))
                direction = str(fill.get("dir", ""))
                side = "long" if "Long" in direction else "short" if "Short" in direction else str(fill.get("side", "unknown"))
                slot = self.store.slot_for_oid(self.run_id, oid)
                if slot is None:
                    slot = self.store.slot_for_unmapped_fill(
                        self.run_id,
                        timestamp_ms=int(fill.get("time", now_ms)),
                        side=side,
                    )
                if slot is None:
                    slot = -1
                    self.store.record_event(
                        run_id=self.run_id,
                        timestamp_ms=int(fill.get("time", now_ms)),
                        event_type="unmatched_fill",
                        payload={"oid": oid, "fill_id": str(fill.get("tid", ""))},
                    )
                fill_id = str(fill.get("tid") or f"{fill.get('hash', '')}:{oid}:{fill.get('time', 0)}")
                self.store.record_fill(
                    run_id=self.run_id,
                    slot=slot,
                    fill_id=fill_id,
                    side=side,
                    size=Decimal(str(fill.get("sz", "0"))),
                    price=Decimal(str(fill.get("px", "0"))),
                    fee=Decimal(str(fill.get("fee", "0"))),
                    closed_pnl=Decimal(str(fill.get("closedPnl", "0"))),
                    timestamp_ms=int(fill.get("time", now_ms)),
                )
        if callable(get_funding):
            for funding in get_funding(self.started_at_ms, now_ms):
                delta = funding.get("delta", {})
                if delta.get("coin") not in {None, self.settings.coin}:
                    continue
                timestamp = int(funding.get("time", now_ms))
                funding_id = str(funding.get("hash") or f"funding:{timestamp}:{delta.get('usdc', '0')}")
                self.store.record_funding(
                    run_id=self.run_id,
                    funding_id=funding_id,
                    amount=Decimal(str(delta.get("usdc", "0"))),
                    timestamp_ms=timestamp,
                )
        self.daily_realized_pnl = self.store.realized_net_pnl(self.run_id)

    def finalize(self, *, now_ms: int) -> bool:
        cleanup_ok = self.cleanup()
        self.sync_exchange_evidence(now_ms=now_ms)
        market = self.exchange.get_market_observation(self.settings.coin, now_ms=now_ms)
        self._record_episode_excursion(
            slot=self.last_slot,
            observation=market,
            end_ms=now_ms,
        )
        account = self.exchange.get_account_snapshot(self.settings.coin)
        self.store.record_equity(
            run_id=self.run_id,
            timestamp_ms=now_ms,
            equity=account.equity,
            withdrawable=account.withdrawable,
            mark=market.mark,
        )
        self.store.finish_run(
            run_id=self.run_id,
            completed_at_ms=now_ms,
            final_equity=account.equity,
            final_mark=market.mark,
            status="completed" if cleanup_ok else "cleanup_failed",
        )
        return cleanup_ok

    def _record_episode_excursion(
        self,
        *,
        slot: int,
        observation: Any,
        end_ms: int,
    ) -> None:
        cycle = self.store.get_cycle(self.run_id, slot)
        if cycle is None or cycle["status"] not in {"ordered", "simulated"}:
            return
        decision = cycle.get("decision")
        plan = decision.get("plan") if isinstance(decision, dict) else None
        if not isinstance(plan, dict):
            return
        start_ms = int(cycle["scheduled_at_ms"])
        close_row = self.store.connection.execute(
            """
            SELECT MIN(timestamp_ms) AS closed_at_ms FROM fills
            WHERE run_id=? AND slot=? AND CAST(closed_pnl AS REAL) != 0
              AND timestamp_ms>? AND timestamp_ms<=?
            """,
            (self.run_id, slot, start_ms, end_ms),
        ).fetchone()
        if close_row is not None and close_row["closed_at_ms"] is not None:
            end_ms = int(close_row["closed_at_ms"])
        estimate = estimate_mfe_mae_pct(
            candles=observation.candles,
            side=str(decision.get("side")),
            entry_price=float(plan["entry_price"]),
            start_ms=start_ms,
            end_ms=end_ms,
        )
        if estimate is None:
            return
        mfe_pct, mae_pct = estimate
        self.store.record_episode_metric(
            run_id=self.run_id,
            slot=slot,
            mfe_pct=Decimal(str(mfe_pct)),
            mae_pct=Decimal(str(mae_pct)),
            method="1m_candle_estimate",
        )

    def record_missed(self, *, slot: int, scheduled_at_ms: int) -> None:
        strategy = self.store.load_latest_strategy(self.run_id) or initial_strategy()
        if self.store.reserve_cycle(
            self.run_id,
            slot,
            scheduled_at_ms=scheduled_at_ms,
            strategy_version=int(strategy["version"]),
        ):
            self._fail_cycle(slot, "missed", "schedule_late")

    def review_once(self, *, review_index: int, review_cycle: int, now_ms: int) -> None:
        if self.reviewer is None:
            return
        self.sync_exchange_evidence(now_ms=now_ms)
        strategy = self.store.load_latest_strategy(self.run_id) or initial_strategy()
        closed = self.store.recent_closed_trades(self.run_id, limit=100)
        try:
            result = self.reviewer.review(
                strategy=strategy,
                closed_trades=closed,
                review_cycle=review_cycle,
            )
            self.store.save_strategy_version(
                run_id=self.run_id,
                version=int(result.state["version"]),
                parent_version=int(strategy["version"]),
                state=result.state,
                patch=result.patch,
                model=result.model,
                created_at_ms=now_ms,
            )
            self.store.record_review(
                run_id=self.run_id,
                review_index=review_index,
                created_at_ms=now_ms,
                model=result.model,
                status="accepted",
                input_payload={"strategy": strategy, "closed_trades": closed},
                output_payload=result.patch,
                error_type=None,
            )
            self.store.record_patch(
                run_id=self.run_id,
                base_version=int(strategy["version"]),
                next_version=int(result.state["version"]),
                patch=result.patch,
                accepted=True,
                reason="review accepted",
                created_at_ms=now_ms,
            )
        except AgentDecisionError as exc:
            self.store.record_review(
                run_id=self.run_id,
                review_index=review_index,
                created_at_ms=now_ms,
                model=getattr(self.reviewer, "model", "unknown"),
                status="rejected",
                input_payload={"strategy": strategy, "closed_trades": closed},
                output_payload=None,
                error_type=exc.error_type,
            )

    def cleanup(self) -> bool:
        account = self.exchange.get_account_snapshot(self.settings.coin)
        if account.unknown_exposure:
            return False
        known = self.store.known_cloids(self.run_id)
        open_cloids = {
            str(order.get("cloid"))
            for order in account.open_orders
            if order.get("cloid") is not None
        }
        if any(cloid not in known for cloid in open_cloids):
            return False
        try:
            if open_cloids:
                self.exchange.cancel_bot_orders(self.settings.coin, sorted(open_cloids))
            if account.position_size != 0:
                self.exchange.close_position(self.settings.coin)
            for _ in range(5):
                after = self.exchange.get_account_snapshot(self.settings.coin)
                if after.position_size == 0 and not after.open_orders:
                    return True
                self.sleeper(1.0)
        except Exception:
            return False
        return False


class LocalRunner:
    def __init__(
        self,
        *,
        service: Any,
        cycles: int,
        interval_seconds: int,
        review_every_cycles: int,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.service = service
        self.cycles = cycles
        self.interval_seconds = interval_seconds
        self.review_every_cycles = review_every_cycles
        self.clock_ms = clock_ms
        self.monotonic = monotonic
        self.sleeper = sleeper

    def run(self) -> RunSummary:
        start_ms = self.clock_ms()
        start_monotonic = self.monotonic()
        reviews = 0
        for slot in range(self.cycles):
            target = start_monotonic + slot * self.interval_seconds
            delay = target - self.monotonic()
            if delay > 0:
                self.sleeper(delay)
            elif self.interval_seconds > 0 and -delay >= self.interval_seconds:
                self.service.record_missed(
                    slot=slot,
                    scheduled_at_ms=start_ms + slot * self.interval_seconds * 1000,
                )
                continue
            if slot > 0 and slot % self.review_every_cycles == 0:
                self.service.cleanup()
                reviews += 1
                self.service.review_once(
                    review_index=reviews,
                    review_cycle=slot,
                    now_ms=self.clock_ms(),
                )
            self.service.run_once(
                slot=slot,
                scheduled_at_ms=start_ms + slot * self.interval_seconds * 1000,
            )

        final_target = start_monotonic + self.cycles * self.interval_seconds
        final_delay = final_target - self.monotonic()
        if self.cycles and final_delay > 0:
            self.sleeper(final_delay)

        cleanup_ok = self.service.cleanup()
        if self.cycles and self.cycles % self.review_every_cycles == 0:
            reviews += 1
            self.service.review_once(
                review_index=reviews,
                review_cycle=self.cycles,
                now_ms=self.clock_ms(),
            )
        return RunSummary(self.cycles, reviews, cleanup_ok)
