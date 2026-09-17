from decimal import Decimal
from pathlib import Path

import pytest

from llm_position_management.policy import ScriptedPolicy
from llm_position_management.runner import PositionRunner, RunnerConfig
from llm_position_management.store import RunStore, StoreError
from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import ExecutionCosts, RiskLimits
from trading_core.market_data.models import MarketTick


def _initial() -> AccountSnapshot:
    return AccountSnapshot(0, Decimal("0"), None, Decimal("1000"), Decimal("1000"), Decimal("0"), None, None, (), Decimal("1000"), Decimal("0"), Decimal("1000"))


def _limits() -> RiskLimits:
    return RiskLimits(Decimal("250"), Decimal("250"), Decimal("10"), Decimal("10"), Decimal("20"), Decimal("25"), 86_400_000)


def _tick(timestamp_ms: int) -> MarketTick:
    return MarketTick(timestamp_ms, Decimal("50000"), Decimal("50000"), Decimal("50000"), Decimal("50000"))


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

    assert [record.decision_id for record in result.records] == ["fixture:300000"]
    assert result.final_snapshot.signed_quantity == Decimal("0.0025")
    assert store.decision_ids("fixture") == {"fixture:0", "fixture:300000"}


def test_store_refuses_to_mix_different_fingerprints_in_one_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run.db")
    store.begin("fixture", "a" * 64)

    with pytest.raises(StoreError, match="fingerprint"):
        store.begin("fixture", "b" * 64)
