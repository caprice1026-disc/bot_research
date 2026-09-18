"""Sequential, offline target-position runner with no exchange or model side effects."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import (
    ExecutionCosts,
    PlanResult,
    PositionTarget,
    RiskLimits,
    freeze_target_quantity,
    plan_position_delta,
)
from trading_core.market_data.models import MarketSnapshot, MarketTick
from trading_core.simulation.position_account import AccountEvent, PositionAccount

from .decisions import DecisionContext, DecisionError, TargetDecision, parse_target_decision
from .observations import build_observation
from .policy import PositionPolicy, PolicyResponse
from .store import RunStore


class RunnerError(ValueError):
    """Raised for invalid local runner configuration or unordered fixture input."""


@dataclass(frozen=True)
class RunnerConfig:
    run_id: str
    decision_interval_ms: int
    max_response_age_ms: int
    max_model_cost_usd: Decimal
    max_market_gap_ms: int = 60_000

    def __post_init__(self) -> None:
        if not self.run_id:
            raise RunnerError("run_id is required")
        if self.decision_interval_ms <= 0 or self.max_response_age_ms < 0 or self.max_market_gap_ms <= 0:
            raise RunnerError("decision interval and market timing limits must be positive")
        if not self.max_model_cost_usd.is_finite() or self.max_model_cost_usd < 0:
            raise RunnerError("max_model_cost_usd must be finite and non-negative")


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    timestamp_ms: int
    execution_timestamp_ms: int
    status: str
    plan: PlanResult | None
    observation: dict[str, object]
    events: tuple[AccountEvent, ...]
    model_cost_usd: Decimal


@dataclass(frozen=True)
class RunnerResult:
    records: tuple[DecisionRecord, ...]
    events: tuple[AccountEvent, ...]
    final_snapshot: AccountSnapshot
    model_cost_usd: Decimal
    fill_count: int
    pending_safe_close: bool
    market_data_complete: bool


def _market_snapshot(tick: MarketTick) -> MarketSnapshot:
    return MarketSnapshot(
        as_of_ms=tick.timestamp_ms,
        available_at_ms=tick.timestamp_ms,
        mark_price=tick.close_price,
        quantity_step=Decimal("0.0001"),
    )


def _target_from_decision(decision: TargetDecision) -> PositionTarget:
    return PositionTarget(
        decision_id=decision.decision_id,
        hold=decision.intent == "hold",
        target_fraction=decision.target_fraction,
        frozen_target_quantity=None,
        stop_price=decision.stop_price,
        source_position_version=decision.source_position_version,
    )


def _event_from_payload(payload: dict[str, object]) -> AccountEvent:
    return AccountEvent(
        kind=str(payload["kind"]),
        timestamp_ms=int(payload["timestamp_ms"]),
        quantity=Decimal(str(payload["quantity"])),
        price=Decimal(str(payload["price"])) if payload["price"] is not None else None,
        amount=Decimal(str(payload["amount"])),
        reason=str(payload["reason"]) if payload["reason"] is not None else None,
    )


def _plan_from_payload(payload: dict[str, object] | None) -> PlanResult | None:
    if payload is None:
        return None
    return PlanResult(
        status=str(payload["status"]),
        reason=str(payload["reason"]) if payload["reason"] is not None else None,
        delta_quantity=Decimal(str(payload["delta_quantity"])),
        reduce_only=bool(payload["reduce_only"]),
        target_quantity=Decimal(str(payload["target_quantity"])),
        stop_price=Decimal(str(payload["stop_price"])) if payload["stop_price"] is not None else None,
        expected_cost=Decimal(str(payload["expected_cost"])),
        intent_id=str(payload["intent_id"]),
    )


def _record_from_payload(payload: dict[str, object]) -> DecisionRecord:
    timestamp_ms = int(payload["timestamp_ms"])
    return DecisionRecord(
        decision_id=str(payload["decision_id"]),
        timestamp_ms=timestamp_ms,
        execution_timestamp_ms=int(payload.get("execution_timestamp_ms", timestamp_ms)),
        status=str(payload["status"]),
        plan=_plan_from_payload(payload["plan"]),
        observation=dict(payload["observation"]),
        events=tuple(_event_from_payload(event) for event in payload["events"]),
        model_cost_usd=Decimal(str(payload["model_cost_usd"])),
    )


class PositionRunner:
    """One account, one ordered market stream, and at most one response per slot."""

    def __init__(
        self,
        *,
        initial: AccountSnapshot,
        limits: RiskLimits,
        costs: ExecutionCosts,
        config: RunnerConfig,
        policy: PositionPolicy,
        store: RunStore | None = None,
        config_fingerprint: str = "offline-unsaved",
    ) -> None:
        self._limits = limits
        self._costs = costs
        self._config = config
        self._policy = policy
        self._store = store
        self._records: list[DecisionRecord] = []
        self._last_market_tick: MarketTick | None = None
        if store is None:
            self._account = PositionAccount(initial, limits, costs)
            self._seen_slots: set[str] = set()
            self._model_cost = Decimal("0")
            self._reserved_model_cost = Decimal("0")
            self._consecutive_failures = 0
            self._events: list[AccountEvent] = []
            self._pending_safe_close = False
            self._market_data_complete = True
        else:
            store.begin(config.run_id, config_fingerprint)
            persisted = store.load_state(config.run_id)
            if persisted is None:
                self._account = PositionAccount(initial, limits, costs)
                self._consecutive_failures = 0
                self._pending_safe_close = False
                self._market_data_complete = True
            else:
                self._account = PositionAccount(persisted.snapshot, limits, costs)
                self._account.restore_tick(persisted.tick)
                self._consecutive_failures = persisted.consecutive_failures
                self._pending_safe_close = persisted.pending_safe_close
                self._market_data_complete = persisted.market_data_complete
                self._last_market_tick = persisted.tick
            self._seen_slots = store.claimed_ids(config.run_id)
            self._model_cost = store.total_model_cost(config.run_id)
            self._reserved_model_cost = store.reserved_model_cost(config.run_id)
            self._events = list(store.account_events(config.run_id))
            self._records = [_record_from_payload(payload) for payload in store.decision_payloads(config.run_id)]
        self._fill_count = sum(event.kind == "fill" for event in self._events)

    def _persist_market_state(self, tick: MarketTick, events: tuple[AccountEvent, ...]) -> None:
        self._events.extend(events)
        self._fill_count += sum(event.kind == "fill" for event in events)
        if self._store is not None:
            self._store.save_state(
                self._config.run_id,
                self._account.snapshot(tick.timestamp_ms),
                tick,
                consecutive_failures=self._consecutive_failures,
                model_cost_usd=self._model_cost,
                pending_safe_close=self._pending_safe_close,
                market_data_complete=self._market_data_complete,
                events=events,
            )

    def _advance_tick(self, tick: MarketTick, funding_rate: Decimal | None) -> tuple[AccountEvent, ...]:
        previous_tick = self._last_market_tick
        events = self._account.advance_to(tick, funding_rate=funding_rate)
        if (
            previous_tick is not None
            and tick.timestamp_ms > previous_tick.timestamp_ms
            and tick.timestamp_ms - previous_tick.timestamp_ms > self._config.max_market_gap_ms
        ):
            self._market_data_complete = False
            events = (
                AccountEvent(
                    "market_data_gap",
                    tick.timestamp_ms,
                    reason=f"missing_market_data_after_{previous_tick.timestamp_ms}",
                ),
                *events,
            )
        self._last_market_tick = tick
        self._persist_market_state(tick, events)
        return events

    def _record(
        self,
        *,
        decision_id: str,
        decision_tick: MarketTick,
        execution_tick: MarketTick,
        status: str,
        observation: dict[str, object],
        plan: PlanResult | None = None,
        events: tuple[AccountEvent, ...] = (),
        model_cost_usd: Decimal = Decimal("0"),
    ) -> None:
        record = DecisionRecord(
            decision_id=decision_id,
            timestamp_ms=decision_tick.timestamp_ms,
            execution_timestamp_ms=execution_tick.timestamp_ms,
            status=status,
            plan=plan,
            observation=observation,
            events=events,
            model_cost_usd=model_cost_usd,
        )
        self._records.append(record)
        self._events.extend(events)
        self._fill_count += sum(event.kind == "fill" for event in events)
        if self._store is not None:
            self._store.record(
                self._config.run_id,
                record,
                self._account.snapshot(execution_tick.timestamp_ms),
                execution_tick,
                consecutive_failures=self._consecutive_failures,
                model_cost_usd=self._model_cost,
                pending_safe_close=self._pending_safe_close,
                market_data_complete=self._market_data_complete,
                events=events,
            )

    def _safe_close(
        self,
        decision_id: str,
        decision_tick: MarketTick,
        execution_tick: MarketTick,
        observation: dict[str, object],
        *,
        model_cost_usd: Decimal,
    ) -> None:
        snapshot = self._account.snapshot(execution_tick.timestamp_ms)
        target = freeze_target_quantity(
            PositionTarget(
                decision_id=f"{decision_id}:safe-close",
                hold=False,
                target_fraction=Decimal("0"),
                frozen_target_quantity=None,
                stop_price=None,
                source_position_version=snapshot.position_version,
            ),
            _market_snapshot(execution_tick),
            self._limits,
        )
        plan = plan_position_delta(target, snapshot, _market_snapshot(execution_tick), self._limits, costs=self._costs)
        events = self._account.apply_plan(plan, executable_at_ms=execution_tick.timestamp_ms)
        self._record(
            decision_id=decision_id,
            decision_tick=decision_tick,
            execution_tick=execution_tick,
            status="forced_safe_close",
            observation=observation,
            plan=plan,
            events=events,
            model_cost_usd=model_cost_usd,
        )

    def _failure(
        self,
        decision_id: str,
        decision_tick: MarketTick,
        execution_tick: MarketTick | None,
        observation: dict[str, object],
        status: str,
        *,
        model_cost_usd: Decimal,
    ) -> None:
        self._consecutive_failures += 1
        record_tick = execution_tick or decision_tick
        snapshot = self._account.snapshot(record_tick.timestamp_ms)
        if self._consecutive_failures >= 3 and snapshot.signed_quantity != 0:
            if execution_tick is None:
                self._pending_safe_close = True
                self._record(
                    decision_id=decision_id,
                    decision_tick=decision_tick,
                    execution_tick=record_tick,
                    status="safe_close_pending",
                    observation=observation,
                    model_cost_usd=model_cost_usd,
                )
                return
            self._pending_safe_close = False
            self._safe_close(
                decision_id,
                decision_tick,
                execution_tick,
                observation,
                model_cost_usd=model_cost_usd,
            )
            return
        self._record(
            decision_id=decision_id,
            decision_tick=decision_tick,
            execution_tick=record_tick,
            status=status,
            observation=observation,
            model_cost_usd=model_cost_usd,
        )

    def _reserve_cost(self, observation: dict[str, object], decision_id: str) -> Decimal | None:
        try:
            reserved = self._policy.reserve_cost_usd(observation, decision_id)
        except Exception:
            return None
        if not isinstance(reserved, Decimal) or not reserved.is_finite() or reserved < 0:
            return None
        return reserved

    def _call_policy(
        self,
        observation: dict[str, object],
        decision_id: str,
        decision_tick: MarketTick,
    ) -> PolicyResponse | None:
        reserved = self._reserve_cost(observation, decision_id)
        if reserved is None:
            self._failure(
                decision_id,
                decision_tick,
                None,
                observation,
                "invalid_cost_reservation",
                model_cost_usd=Decimal("0"),
            )
            return None
        if self._model_cost + self._reserved_model_cost + reserved > self._config.max_model_cost_usd:
            self._record(
                decision_id=decision_id,
                decision_tick=decision_tick,
                execution_tick=decision_tick,
                status="model_budget_exhausted",
                observation=observation,
            )
            return None
        if self._store is not None:
            self._store.reserve_model_request(self._config.run_id, decision_id, reserved)
        self._reserved_model_cost += reserved
        try:
            response = self._policy.decide(observation, decision_id)
        except Exception:
            if self._store is not None:
                self._store.mark_model_request_unknown(self._config.run_id, decision_id)
            self._failure(
                decision_id,
                decision_tick,
                None,
                observation,
                "model_request_unknown",
                model_cost_usd=Decimal("0"),
            )
            return None
        actual_cost = response.estimated_cost_usd if isinstance(response, PolicyResponse) else None
        if actual_cost is None or not actual_cost.is_finite() or actual_cost < 0:
            if self._store is not None:
                self._store.mark_model_request_unknown(self._config.run_id, decision_id)
            self._failure(
                decision_id,
                decision_tick,
                None,
                observation,
                "invalid_response_cost",
                model_cost_usd=Decimal("0"),
            )
            return None
        if self._store is not None:
            self._store.settle_model_request(self._config.run_id, decision_id, actual_cost)
        self._reserved_model_cost -= reserved
        self._model_cost += actual_cost
        return response

    @staticmethod
    def _first_executable_index(ticks: list[MarketTick], start: int, received_at_ms: int) -> int | None:
        for index in range(start, len(ticks)):
            if ticks[index].timestamp_ms >= received_at_ms:
                return index
        return None

    @staticmethod
    def _has_market_data_gap(ticks: list[MarketTick], start: int, end: int, max_gap_ms: int) -> bool:
        return any(
            ticks[index].timestamp_ms - ticks[index - 1].timestamp_ms > max_gap_ms
            for index in range(start + 1, end + 1)
        )

    def _resolve_pending_safe_close(self, tick: MarketTick) -> bool:
        """Close at the first known price after an earlier response-less safety failure."""

        if not self._pending_safe_close:
            return False
        snapshot = self._account.snapshot(tick.timestamp_ms)
        decision_id = f"{self._config.run_id}:{tick.timestamp_ms}"
        if decision_id in self._seen_slots:
            decision_id = f"{decision_id}:safe-close"
        self._seen_slots.add(decision_id)
        observation = build_observation(snapshot, tick, self._limits, previous_status="safe_close_pending")
        if snapshot.signed_quantity == 0:
            self._pending_safe_close = False
            self._record(
                decision_id=decision_id,
                decision_tick=tick,
                execution_tick=tick,
                status="safe_close_already_flat",
                observation=observation,
            )
            return True
        self._pending_safe_close = False
        self._safe_close(
            decision_id,
            tick,
            tick,
            observation,
            model_cost_usd=Decimal("0"),
        )
        return True

    def run(
        self,
        ticks: Iterable[MarketTick],
        *,
        funding_by_timestamp_ms: dict[int, Decimal] | None = None,
    ) -> RunnerResult:
        """Run ordered fixture ticks. Repeating a slot produces no second decision."""

        sequence = list(ticks)
        if not sequence:
            raise RunnerError("at least one market tick is required")
        funding = funding_by_timestamp_ms or {}
        latest_tick: MarketTick | None = None
        index = 0
        while index < len(sequence):
            decision_tick = sequence[index]
            latest_tick = decision_tick
            self._advance_tick(decision_tick, funding.get(decision_tick.timestamp_ms))
            if self._resolve_pending_safe_close(decision_tick):
                index += 1
                continue
            if decision_tick.timestamp_ms % self._config.decision_interval_ms:
                index += 1
                continue
            decision_id = f"{self._config.run_id}:{decision_tick.timestamp_ms}"
            if decision_id in self._seen_slots:
                index += 1
                continue
            self._seen_slots.add(decision_id)
            snapshot = self._account.snapshot(decision_tick.timestamp_ms)
            previous_status = self._records[-1].status if self._records else None
            observation = build_observation(snapshot, decision_tick, self._limits, previous_status=previous_status)
            if not self._market_data_complete:
                self._record(
                    decision_id=decision_id,
                    decision_tick=decision_tick,
                    execution_tick=decision_tick,
                    status="market_data_incomplete",
                    observation=observation,
                )
                index += 1
                continue
            response = self._call_policy(observation, decision_id, decision_tick)
            if response is None:
                index += 1
                continue
            if not isinstance(response.received_at_ms, int):
                self._failure(
                    decision_id,
                    decision_tick,
                    None,
                    observation,
                    "invalid_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                index += 1
                continue
            execution_index = self._first_executable_index(sequence, index, response.received_at_ms)
            execution_tick = None
            if execution_index is not None:
                for advanced_index in range(index + 1, execution_index + 1):
                    execution_tick = sequence[advanced_index]
                    latest_tick = execution_tick
                    self._advance_tick(execution_tick, funding.get(execution_tick.timestamp_ms))
                execution_tick = sequence[execution_index]
            if response.received_at_ms < decision_tick.timestamp_ms:
                self._failure(
                    decision_id,
                    decision_tick,
                    execution_tick,
                    observation,
                    "invalid_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                index = (execution_index + 1) if execution_index is not None else index + 1
                continue
            if response.received_at_ms > decision_tick.timestamp_ms + self._config.max_response_age_ms:
                self._failure(
                    decision_id,
                    decision_tick,
                    execution_tick,
                    observation,
                    "stale_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                index = (execution_index + 1) if execution_index is not None else index + 1
                continue
            try:
                if response.payload is None:
                    raise DecisionError("model response is unavailable")
                decision = parse_target_decision(response.payload, DecisionContext(decision_id, snapshot.position_version))
            except DecisionError:
                self._failure(
                    decision_id,
                    decision_tick,
                    execution_tick,
                    observation,
                    "invalid_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                index = (execution_index + 1) if execution_index is not None else index + 1
                continue
            target = freeze_target_quantity(_target_from_decision(decision), _market_snapshot(decision_tick), self._limits)
            if execution_tick is None or execution_index is None:
                self._record(
                    decision_id=decision_id,
                    decision_tick=decision_tick,
                    execution_tick=decision_tick,
                    status="execution_unavailable",
                    observation=observation,
                    model_cost_usd=response.estimated_cost_usd,
                )
                index += 1
                continue
            if self._has_market_data_gap(sequence, index, execution_index, self._config.max_market_gap_ms):
                self._consecutive_failures = 0
                self._record(
                    decision_id=decision_id,
                    decision_tick=decision_tick,
                    execution_tick=execution_tick,
                    status="market_data_gap",
                    observation=observation,
                    model_cost_usd=response.estimated_cost_usd,
                )
                index = execution_index + 1
                continue
            execution_snapshot = self._account.snapshot(execution_tick.timestamp_ms)
            if execution_snapshot.position_version != snapshot.position_version:
                self._record(
                    decision_id=decision_id,
                    decision_tick=decision_tick,
                    execution_tick=execution_tick,
                    status="stale_position",
                    observation=observation,
                    model_cost_usd=response.estimated_cost_usd,
                )
                index = execution_index + 1
                continue
            self._consecutive_failures = 0
            plan = plan_position_delta(target, execution_snapshot, _market_snapshot(execution_tick), self._limits, costs=self._costs)
            if plan.status == "planned":
                plan_events = self._account.apply_plan(plan, executable_at_ms=execution_tick.timestamp_ms)
                status = "planned"
            elif plan.reason == "hold":
                plan_events = ()
                status = "hold"
            else:
                plan_events = ()
                status = plan.status
            self._record(
                decision_id=decision_id,
                decision_tick=decision_tick,
                execution_tick=execution_tick,
                status=status,
                observation=observation,
                plan=plan,
                events=plan_events,
                model_cost_usd=response.estimated_cost_usd,
            )
            index = execution_index + 1
        assert latest_tick is not None
        return RunnerResult(
            records=tuple(self._records),
            events=tuple(self._events),
            final_snapshot=self._account.snapshot(latest_tick.timestamp_ms),
            model_cost_usd=self._model_cost,
            fill_count=self._fill_count,
            pending_safe_close=self._pending_safe_close,
            market_data_complete=self._market_data_complete,
        )
