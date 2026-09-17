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
from .policy import PositionPolicy
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
    status: str
    plan: PlanResult | None
    observation: dict[str, object]
    events: tuple[AccountEvent, ...]
    model_cost_usd: Decimal


@dataclass(frozen=True)
class RunnerResult:
    records: tuple[DecisionRecord, ...]
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
        if store is None:
            self._account = PositionAccount(initial, limits, costs)
            self._seen_slots: set[str] = set()
            self._model_cost = Decimal("0")
        else:
            store.begin(config.run_id, config_fingerprint)
            persisted = store.load_latest(config.run_id)
            if persisted is None:
                self._account = PositionAccount(initial, limits, costs)
            else:
                persisted_snapshot, persisted_tick = persisted
                self._account = PositionAccount(persisted_snapshot, limits, costs)
                self._account.restore_tick(persisted_tick)
            self._seen_slots = store.decision_ids(config.run_id)
            self._model_cost = store.total_model_cost(config.run_id)
        self._records: list[DecisionRecord] = []
        self._fill_count = 0
        self._consecutive_failures = 0

    def _record(
        self,
        *,
        decision_id: str,
        tick: MarketTick,
        status: str,
        observation: dict[str, object],
        plan: PlanResult | None = None,
        events: tuple[AccountEvent, ...] = (),
        model_cost_usd: Decimal = Decimal("0"),
    ) -> None:
        self._fill_count += sum(event.kind == "fill" for event in events)
        record = DecisionRecord(decision_id, tick.timestamp_ms, status, plan, observation, events, model_cost_usd)
        self._records.append(record)
        if self._store is not None:
            self._store.record(
                self._config.run_id,
                record,
                self._account.snapshot(tick.timestamp_ms),
                tick,
            )

    def _safe_close(
        self,
        decision_id: str,
        tick: MarketTick,
        observation: dict[str, object],
        *,
        model_cost_usd: Decimal,
    ) -> None:
        snapshot = self._account.snapshot(tick.timestamp_ms)
        target = freeze_target_quantity(
            PositionTarget(
                decision_id=f"{decision_id}:safe-close",
                hold=False,
                target_fraction=Decimal("0"),
                frozen_target_quantity=None,
                stop_price=None,
                source_position_version=snapshot.position_version,
            ),
            _market_snapshot(tick),
            self._limits,
        )
        plan = plan_position_delta(target, snapshot, _market_snapshot(tick), self._limits, costs=self._costs)
        events = self._account.apply_plan(plan, executable_at_ms=tick.timestamp_ms)
        self._record(
            decision_id=decision_id,
            tick=tick,
            status="forced_safe_close",
            observation=observation,
            plan=plan,
            events=events,
            model_cost_usd=model_cost_usd,
        )

    def _failure(
        self,
        decision_id: str,
        tick: MarketTick,
        observation: dict[str, object],
        status: str,
        *,
        model_cost_usd: Decimal,
    ) -> None:
        self._consecutive_failures += 1
        snapshot = self._account.snapshot(tick.timestamp_ms)
        if self._consecutive_failures >= 3 and snapshot.signed_quantity != 0:
            self._safe_close(decision_id, tick, observation, model_cost_usd=model_cost_usd)
            return
        self._record(
            decision_id=decision_id,
            tick=tick,
            status=status,
            observation=observation,
            model_cost_usd=model_cost_usd,
        )

    def run(
        self,
        ticks: Iterable[MarketTick],
        *,
        funding_by_timestamp_ms: dict[int, Decimal] | None = None,
    ) -> RunnerResult:
        """Run ordered fixture ticks.  Repeating a slot produces no second decision."""

        latest_tick: MarketTick | None = None
        funding = funding_by_timestamp_ms or {}
        for tick in ticks:
            latest_tick = tick
            events = self._account.advance_to(tick, funding_rate=funding.get(tick.timestamp_ms))
            self._fill_count += sum(event.kind == "fill" for event in events)
            if tick.timestamp_ms % self._config.decision_interval_ms:
                continue
            decision_id = f"{self._config.run_id}:{tick.timestamp_ms}"
            if decision_id in self._seen_slots:
                continue
            self._seen_slots.add(decision_id)
            snapshot = self._account.snapshot(tick.timestamp_ms)
            previous_status = self._records[-1].status if self._records else None
            observation = build_observation(snapshot, tick, self._limits, previous_status=previous_status)
            response = self._policy.decide(observation, decision_id)
            if self._model_cost + response.estimated_cost_usd > self._config.max_model_cost_usd:
                self._record(decision_id=decision_id, tick=tick, status="model_budget_exhausted", observation=observation)
                continue
            self._model_cost += response.estimated_cost_usd
            if response.received_at_ms > tick.timestamp_ms + self._config.max_response_age_ms:
                self._failure(
                    decision_id,
                    tick,
                    observation,
                    "stale_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                continue
            try:
                if response.payload is None:
                    raise DecisionError("model response is unavailable")
                decision = parse_target_decision(response.payload, DecisionContext(decision_id, snapshot.position_version))
            except DecisionError:
                self._failure(
                    decision_id,
                    tick,
                    observation,
                    "invalid_response",
                    model_cost_usd=response.estimated_cost_usd,
                )
                continue
            self._consecutive_failures = 0
            target = freeze_target_quantity(_target_from_decision(decision), _market_snapshot(tick), self._limits)
            plan = plan_position_delta(target, snapshot, _market_snapshot(tick), self._limits, costs=self._costs)
            if plan.status == "planned":
                plan_events = self._account.apply_plan(plan, executable_at_ms=tick.timestamp_ms)
                self._record(
                    decision_id=decision_id,
                    tick=tick,
                    status="planned",
                    observation=observation,
                    plan=plan,
                    events=plan_events,
                    model_cost_usd=response.estimated_cost_usd,
                )
            elif plan.reason == "hold":
                self._record(
                    decision_id=decision_id,
                    tick=tick,
                    status="hold",
                    observation=observation,
                    plan=plan,
                    model_cost_usd=response.estimated_cost_usd,
                )
            else:
                self._record(
                    decision_id=decision_id,
                    tick=tick,
                    status=plan.status,
                    observation=observation,
                    plan=plan,
                    model_cost_usd=response.estimated_cost_usd,
                )
        if latest_tick is None:
            raise RunnerError("at least one market tick is required")
        return RunnerResult(
            records=tuple(self._records),
            final_snapshot=self._account.snapshot(latest_tick.timestamp_ms),
            model_cost_usd=self._model_cost,
            fill_count=self._fill_count,
        )
