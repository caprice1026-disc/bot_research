from decimal import Decimal

from llm_position_management.policy import PolicyResponse, ScriptedPolicy
from llm_position_management.runner import PositionRunner, RunnerConfig
from trading_core.accounting.models import AccountSnapshot
from trading_core.execution.position_plan import ExecutionCosts, RiskLimits
from trading_core.market_data.models import MarketTick


def _snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        position_version=0,
        signed_quantity=Decimal("0"),
        average_entry_price=None,
        cash=Decimal("1000"),
        equity=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
        opened_at_ms=None,
        stop_price=None,
        pending_order_ids=(),
        day_start_equity=Decimal("1000"),
        daily_realized_pnl=Decimal("0"),
        peak_equity=Decimal("1000"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        exposure_anchor_usd=Decimal("250"),
        max_position_notional_usd=Decimal("250"),
        min_notional_usd=Decimal("10"),
        risk_per_position_pct=Decimal("10"),
        max_daily_loss_pct=Decimal("20"),
        max_drawdown_pct=Decimal("25"),
        max_hold_ms=86_400_000,
    )


def _tick(timestamp_ms: int, price: str) -> MarketTick:
    return MarketTick(
        timestamp_ms=timestamp_ms,
        open_price=Decimal(price),
        high_price=Decimal(price),
        low_price=Decimal(price),
        close_price=Decimal(price),
    )


def _payload(decision_id: str, intent: str, fraction: str | None, stop: str | None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "decision_id": decision_id,
        "intent": intent,
        "target_fraction": fraction,
        "stop_price": stop,
        "thesis": "fixture decision",
        "invalidation": "fixture invalidation",
    }


def _runner(policy: ScriptedPolicy, **overrides: object) -> PositionRunner:
    values: dict[str, object] = {
        "run_id": "fixture",
        "decision_interval_ms": 300_000,
        "max_response_age_ms": 60_000,
        "max_model_cost_usd": Decimal("1"),
    }
    values.update(overrides)
    return PositionRunner(
        initial=_snapshot(),
        limits=_limits(),
        costs=ExecutionCosts(fee_rate=Decimal("0"), spread_bps=Decimal("0"), slippage_bps=Decimal("0")),
        config=RunnerConfig(**values),
        policy=policy,
    )


def test_runner_keeps_one_stateful_position_across_open_hold_add_reduce_and_close() -> None:
    policy = ScriptedPolicy(
        {
            "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
            "fixture:300000": _payload("fixture:300000", "hold", None, None),
            "fixture:600000": _payload("fixture:600000", "set_target", "1", "50000"),
            "fixture:900000": _payload("fixture:900000", "set_target", "0.5", "50000"),
            "fixture:1200000": _payload("fixture:1200000", "set_target", "0", None),
        }
    )

    result = _runner(policy).run(
        [_tick(0, "50000"), _tick(300_000, "51000"), _tick(600_000, "52000"), _tick(900_000, "51000"), _tick(1_200_000, "51000")]
    )

    assert [record.status for record in result.records] == ["planned", "hold", "planned", "planned", "planned"]
    assert result.fill_count == 4
    assert result.final_snapshot.signed_quantity == Decimal("0")


def test_duplicate_slot_is_idempotent_and_does_not_submit_a_second_delta() -> None:
    policy = ScriptedPolicy({"fixture:0": _payload("fixture:0", "set_target", "0.5", "49000")})

    result = _runner(policy).run([_tick(0, "50000"), _tick(0, "50000")])

    assert len(result.records) == 1
    assert result.fill_count == 1


def test_three_invalid_responses_close_the_existing_position_without_treating_error_as_hold() -> None:
    invalid = {"schema_version": 1}
    policy = ScriptedPolicy(
        {
            "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
            "fixture:300000": invalid,
            "fixture:600000": invalid,
            "fixture:900000": invalid,
        }
    )

    result = _runner(policy).run([_tick(0, "50000"), _tick(300_000, "50000"), _tick(600_000, "50000"), _tick(900_000, "50000")])

    assert [record.status for record in result.records][-1] == "forced_safe_close"
    assert result.final_snapshot.signed_quantity == Decimal("0")


def test_stale_model_response_is_recorded_but_cannot_open_a_position() -> None:
    policy = ScriptedPolicy(
        {
            "fixture:0": PolicyResponse(
                payload=_payload("fixture:0", "set_target", "0.5", "49000"),
                received_at_ms=60_001,
                estimated_cost_usd=Decimal("0"),
            )
        }
    )

    result = _runner(policy).run([_tick(0, "50000")])

    assert result.records[0].status == "stale_response"
    assert result.final_snapshot.signed_quantity == Decimal("0")


def test_budget_exhaustion_preserves_the_current_protected_position() -> None:
    policy = ScriptedPolicy(
        {
            "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
            "fixture:300000": PolicyResponse(payload=None, received_at_ms=300_000, estimated_cost_usd=Decimal("2")),
        }
    )

    result = _runner(policy, max_model_cost_usd=Decimal("1")).run([_tick(0, "50000"), _tick(300_000, "50000")])

    assert result.records[-1].status == "model_budget_exhausted"
    assert result.final_snapshot.signed_quantity > 0
