from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from llm_position_management.policy import ScriptedPolicy
from llm_position_management.report import build_report
from llm_position_management.runner import PositionRunner, RunnerConfig
from llm_position_management.store import RunStore, StoreError
from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import ExecutionCosts, RiskLimits
from trading_core.market_data.models import MarketTick


def _initial() -> AccountSnapshot:
    return AccountSnapshot(0, Decimal("0"), None, Decimal("1000"), Decimal("1000"), Decimal("0"), None, None, (), Decimal("1000"), Decimal("0"), Decimal("1000"))


def _limits() -> RiskLimits:
    return RiskLimits(Decimal("250"), Decimal("250"), Decimal("10"), Decimal("10"), Decimal("20"), Decimal("25"), 86_400_000)


def _tick(timestamp_ms: int, price: str = "50000", *, low: str | None = None, high: str | None = None) -> MarketTick:
    return MarketTick(
        timestamp_ms,
        Decimal(price),
        Decimal(high or price),
        Decimal(low or price),
        Decimal(price),
    )


def _payload(decision_id: str, intent: str, fraction: str | None, stop: str | None) -> dict[str, object]:
    return {"schema_version": 1, "decision_id": decision_id, "intent": intent, "target_fraction": fraction, "stop_price": stop, "thesis": "fixture", "invalidation": "fixture"}


def _runner(store: RunStore, policy: ScriptedPolicy) -> PositionRunner:
    return PositionRunner(
        initial=_initial(),
        limits=_limits(),
        costs=ExecutionCosts(fee_rate=Decimal("0"), spread_bps=Decimal("0"), slippage_bps=Decimal("0")),
        config=RunnerConfig("fixture", 300_000, 60_000, Decimal("0")),
        policy=policy,
        store=store,
        config_fingerprint="f" * 64,
    )


def test_restart_uses_saved_position_and_never_replays_an_existing_slot(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run.db")
    first = _runner(store, ScriptedPolicy({"fixture:0": _payload("fixture:0", "set_target", "0.5", "49000")}))
    first.run([_tick(0)])

    second = _runner(
        store,
        ScriptedPolicy(
            {
                "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
                "fixture:300000": _payload("fixture:300000", "hold", None, None),
            }
        ),
    )
    result = second.run([_tick(0), _tick(300_000)])

    assert [record.decision_id for record in result.records] == ["fixture:0", "fixture:300000"]
    assert result.final_snapshot.signed_quantity == Decimal("0.0025")
    assert store.decision_ids("fixture") == {"fixture:0", "fixture:300000"}


def test_store_refuses_to_mix_different_fingerprints_in_one_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run.db")
    store.begin("fixture", "a" * 64)

    with pytest.raises(StoreError, match="fingerprint"):
        store.begin("fixture", "b" * 64)


def test_restart_does_not_revive_a_position_closed_by_a_non_decision_stop(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run.db")
    first = _runner(store, ScriptedPolicy({"fixture:0": _payload("fixture:0", "set_target", "0.5", "49000")}))

    result = first.run([_tick(0), _tick(60_000, "48000", low="47900", high="50000")])

    assert result.final_snapshot.signed_quantity == Decimal("0")
    assert result.fill_count == 2
    assert len([event for event in store.account_events("fixture") if event.kind == "fill"]) == 2

    resumed = _runner(store, ScriptedPolicy({})).run([_tick(60_000, "48000"), _tick(120_000, "48000")])

    assert resumed.final_snapshot.signed_quantity == Decimal("0")
    assert resumed.fill_count == 2


def test_restart_restores_consecutive_failures_before_deciding_to_safe_close(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run.db")
    invalid = {"schema_version": 1}
    first = _runner(
        store,
        ScriptedPolicy(
            {
                "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
                "fixture:300000": invalid,
                "fixture:600000": invalid,
            }
        ),
    )
    first.run([_tick(0), _tick(300_000), _tick(600_000)])

    resumed = _runner(store, ScriptedPolicy({"fixture:900000": invalid})).run([_tick(900_000)])

    assert resumed.records[-1].status == "forced_safe_close"
    assert resumed.final_snapshot.signed_quantity == Decimal("0")


class _UnderReservedPolicy:
    def reserve_cost_usd(self, observation: dict[str, object], request_id: str) -> Decimal:
        return Decimal("1")

    def decide(self, observation: dict[str, object], request_id: str):
        from llm_position_management.policy import PolicyResponse

        return PolicyResponse(_payload(request_id, "set_target", "0.5", "49000"), int(observation["as_of_ms"]), Decimal("2"))


def test_store_persists_actual_cost_after_a_pre_call_reservation(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run.db")
    runner = PositionRunner(
        initial=_initial(),
        limits=_limits(),
        costs=ExecutionCosts(fee_rate=Decimal("0"), spread_bps=Decimal("0"), slippage_bps=Decimal("0")),
        config=RunnerConfig("fixture", 300_000, 60_000, Decimal("1")),
        policy=_UnderReservedPolicy(),
        store=store,
        config_fingerprint="f" * 64,
    )

    result = runner.run([_tick(0), _tick(300_000)])

    assert result.model_cost_usd == Decimal("2")
    assert store.total_model_cost("fixture") == Decimal("2")
    assert store.reserved_model_cost("fixture") == Decimal("0")


def test_schema_v1_database_is_migrated_to_the_stateful_run_schema(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=1")

    RunStore(path)

    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert version == 2
    assert {"runner_state", "account_events", "model_requests"} <= tables


def test_resumed_run_reports_the_same_decisions_events_costs_and_account_as_an_uninterrupted_run(tmp_path: Path) -> None:
    ticks = [_tick(0), _tick(300_000), _tick(600_000)]
    decisions = {
        "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
        "fixture:300000": _payload("fixture:300000", "hold", None, None),
        "fixture:600000": _payload("fixture:600000", "set_target", "0", None),
    }
    uninterrupted = _runner(RunStore(tmp_path / "uninterrupted.db"), ScriptedPolicy(decisions)).run(ticks)

    resumed_store = RunStore(tmp_path / "resumed.db")
    _runner(resumed_store, ScriptedPolicy(decisions)).run(ticks[:2])
    resumed = _runner(resumed_store, ScriptedPolicy(decisions)).run(ticks[2:])

    assert resumed.records == uninterrupted.records
    assert resumed.events == uninterrupted.events
    assert resumed.model_cost_usd == uninterrupted.model_cost_usd
    assert resumed.final_snapshot == uninterrupted.final_snapshot
    assert build_report(resumed) == build_report(uninterrupted)
