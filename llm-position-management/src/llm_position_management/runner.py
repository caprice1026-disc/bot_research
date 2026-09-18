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

    def __post_init__(self) -> None:
        if not self.run_id:
            raise RunnerError("run_id is required")
        if self.decision_interval_ms <= 0 or self.max_response_age_ms < 0:
            raise RunnerError("decision interval and response age must be non-negative")
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
        if store is None:
            self._account = PositionAccount(initial, limits, costs)
            self._seen_slots: set[str] = set()
            self._model_cost = Decimal("0")
            self._reserved_model_cost = Decimal("0")
            self._consecutive_failures = 0
            self._events: list[AccountEvent] = []
        else:
            store.begin(config.run_id, config_fingerprint)
            persisted = store.load_state(config.run_id)
            if persisted is None:
                self._account = PositionAccount(initial, limits, costs)
                self._consecutive_failures = 0
            else:
                self._account = PositionAccount(persisted.snapshot, limits, costs)
                self._account.restore_tick(persisted.tick)
                self._consecutive_failures = persisted.consecutive_failures
            self._seen_slots = store.claimed_ids(config.run_id)
            self._model_cost = store.total_model_cost(config.run_id)
            self._reserved_model_cost = store.reserved_model_cost(config.run_id)
            self._events = list(store.account_events(config.run_id))
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
                events=events,
            )

    def _advance_tick(self, tick: MarketTick, funding_rate: Decimal | None) -> tuple[AccountEvent, ...]:
        events = self._account.advance_to(tick, funding_rate=funding_rate)
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
        execution_tick: MarketTick,
        observation: dict[str, object],
        status: str,
        *,
        model_cost_usd: Decimal,
    ) -> None:
        self._consecutive_failures += 1
        snapshot = self._account.snapshot(execution_tick.timestamp_ms)
        if self._consecutive_failures >= 3 and snapshot.signed_quantity != 0:
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
            execution_tick=execution_tick,
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
                decision_tick,
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
                decision_tick,
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
                decision_tick,
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
            response = self._call_policy(observation, decision_id, decision_tick)
            if response is None:
                index += 1
                continue
            if response.received_at_ms < decision_tick.timestamp_ms:
                self._failure(
                    decision_id,
                    decision_tick,
                    decision_tick,
                    observation,
                    "invalid_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                index += 1
                continue
            if response.received_at_ms > decision_tick.timestamp_ms + self._config.max_response_age_ms:
                self._failure(
                    decision_id,
                    decision_tick,
                    decision_tick,
                    observation,
                    "stale_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                index += 1
                continue
            try:
                if response.payload is None:
                    raise DecisionError("model response is unavailable")
                decision = parse_target_decision(response.payload, DecisionContext(decision_id, snapshot.position_version))
            except DecisionError:
                self._failure(
                    decision_id,
                    decision_tick,
                    decision_tick,
                    observation,
                    "invalid_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                index += 1
                continue
            target = freeze_target_quantity(_target_from_decision(decision), _market_snapshot(decision_tick), self._limits)
            execution_index = self._first_executable_index(sequence, index, response.received_at_ms)
            if execution_index is None:
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
            for advanced_index in range(index + 1, execution_index + 1):
                execution_tick = sequence[advanced_index]
                latest_tick = execution_tick
                self._advance_tick(execution_tick, funding.get(execution_tick.timestamp_ms))
            execution_tick = sequence[execution_index]
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
        )
