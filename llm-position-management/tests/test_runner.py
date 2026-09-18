from decimal import Decimal

from llm_position_management.policy import PolicyResponse, ScriptedPolicy
from llm_position_management.report import build_report
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


def _runner(policy: object, **overrides: object) -> PositionRunner:
    values: dict[str, object] = {
        "run_id": "fixture",
        "decision_interval_ms": 300_000,
        "max_response_age_ms": 60_000,
        "max_model_cost_usd": Decimal("1"),
        "max_market_gap_ms": 300_000,
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


class _CountingPolicy:
    def __init__(self, response: PolicyResponse, reservation: Decimal) -> None:
        self.response = response
        self.reservation = reservation
        self.calls = 0

    def reserve_cost_usd(self, observation: dict[str, object], request_id: str) -> Decimal:
        return self.reservation

    def decide(self, observation: dict[str, object], request_id: str) -> PolicyResponse:
        self.calls += 1
        return self.response


def test_budget_reservation_blocks_a_call_before_it_is_made() -> None:
    policy = _CountingPolicy(
        PolicyResponse(
            payload=_payload("fixture:0", "set_target", "0.5", "49000"),
            received_at_ms=0,
            estimated_cost_usd=Decimal("2"),
        ),
        Decimal("2"),
    )

    result = _runner(policy, max_model_cost_usd=Decimal("1")).run([_tick(0, "50000"), _tick(300_000, "50000")])

    assert policy.calls == 0
    assert [record.status for record in result.records] == ["model_budget_exhausted", "model_budget_exhausted"]
    assert result.model_cost_usd == Decimal("0")


def test_incurred_model_cost_is_saved_even_when_it_exceeds_its_reservation() -> None:
    policy = _CountingPolicy(
        PolicyResponse(
            payload=_payload("fixture:0", "set_target", "0.5", "49000"),
            received_at_ms=0,
            estimated_cost_usd=Decimal("2"),
        ),
        Decimal("1"),
    )

    result = _runner(policy, max_model_cost_usd=Decimal("1")).run([_tick(0, "50000"), _tick(300_000, "50000")])

    assert policy.calls == 1
    assert result.records[0].model_cost_usd == Decimal("2")
    assert result.records[1].status == "model_budget_exhausted"
    assert result.model_cost_usd == Decimal("2")


def test_valid_response_executes_at_the_first_market_tick_after_it_is_received() -> None:
    policy = ScriptedPolicy(
        {
            "fixture:0": PolicyResponse(
                payload=_payload("fixture:0", "set_target", "0.5", "49000"),
                received_at_ms=59_000,
                estimated_cost_usd=Decimal("0"),
            )
        }
    )

    result = _runner(policy).run([_tick(0, "50000"), _tick(60_000, "51000")])

    assert result.records[0].execution_timestamp_ms == 60_000
    assert result.records[0].events[0].price == Decimal("51000")
    assert result.final_snapshot.average_entry_price == Decimal("51000")


def test_response_is_not_executed_when_its_position_stops_out_while_waiting() -> None:
    policy = ScriptedPolicy(
        {
            "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
            "fixture:300000": PolicyResponse(
                payload=_payload("fixture:300000", "set_target", "1", "49000"),
                received_at_ms=359_000,
                estimated_cost_usd=Decimal("0"),
            ),
        }
    )
    ticks = [
        _tick(0, "50000"),
        _tick(300_000, "50000"),
        MarketTick(360_000, Decimal("50000"), Decimal("50000"), Decimal("48000"), Decimal("48000")),
    ]

    result = _runner(policy).run(ticks)

    assert result.records[-1].status == "stale_position"
    assert result.final_snapshot.signed_quantity == Decimal("0")
    assert len([event for event in result.events if event.kind == "fill"]) == 2


def test_third_invalid_response_closes_at_the_first_market_tick_after_receipt() -> None:
    invalid = {"schema_version": 1}
    policy = ScriptedPolicy(
        {
            "fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
            "fixture:300000": PolicyResponse(invalid, 300_000, Decimal("0")),
            "fixture:600000": PolicyResponse(invalid, 600_000, Decimal("0")),
            "fixture:900000": PolicyResponse(invalid, 959_000, Decimal("0")),
        }
    )

    result = _runner(policy).run(
        [_tick(0, "50000"), _tick(300_000, "50000"), _tick(600_000, "50000"), _tick(900_000, "50000"), _tick(960_000, "51000")]
    )

    assert result.records[-1].status == "forced_safe_close"
    assert result.records[-1].execution_timestamp_ms == 960_000
    assert result.records[-1].events[0].price == Decimal("51000")


def test_valid_response_is_not_executed_across_a_market_data_gap() -> None:
    policy = ScriptedPolicy(
        {
            "fixture:0": PolicyResponse(
                payload=_payload("fixture:0", "set_target", "0.5", "49000"),
                received_at_ms=59_000,
                estimated_cost_usd=Decimal("0"),
            )
        }
    )

    result = _runner(policy, max_market_gap_ms=60_000).run([_tick(0, "50000"), _tick(300_000, "51000")])

    assert result.records[0].status == "market_data_gap"
    assert result.final_snapshot.signed_quantity == Decimal("0")


class _OpenThenConnectionErrors:
    def reserve_cost_usd(self, observation: dict[str, object], request_id: str) -> Decimal:
        return Decimal("0")

    def decide(self, observation: dict[str, object], request_id: str) -> PolicyResponse:
        if request_id == "fixture:0":
            return PolicyResponse(_payload(request_id, "set_target", "0.5", "49000"), 0, Decimal("0"))
        raise ConnectionError("fixture network failure")


def test_pending_safe_close_from_connection_errors_executes_at_the_next_tick() -> None:
    result = _runner(_OpenThenConnectionErrors()).run(
        [_tick(timestamp_ms, "51000" if timestamp_ms == 960_000 else "50000") for timestamp_ms in range(0, 960_001, 60_000)]
    )

    assert [record.status for record in result.records][-2:] == ["safe_close_pending", "forced_safe_close"]
    assert result.records[-1].execution_timestamp_ms == 960_000
    assert result.records[-1].events[0].price == Decimal("51000")
    assert result.final_snapshot.signed_quantity == Decimal("0")


def test_gap_while_holding_marks_the_result_partial_even_without_a_delayed_response() -> None:
    policy = ScriptedPolicy({"fixture:0": _payload("fixture:0", "set_target", "0.5", "49000")})

    result = _runner(policy, max_market_gap_ms=60_000).run([_tick(0, "50000"), _tick(60_000, "50000"), _tick(300_000, "51000")])

    report = build_report(result)
    assert any(event.kind == "market_data_gap" for event in result.events)
    assert report["status"] == "partial"
    assert report["market_data_complete"] is False


class _OpenThenFailuresThenOpen:
    def reserve_cost_usd(self, observation: dict[str, object], request_id: str) -> Decimal:
        return Decimal("0")

    def decide(self, observation: dict[str, object], request_id: str) -> PolicyResponse:
        if request_id == "fixture:0":
            return PolicyResponse(_payload(request_id, "set_target", "0.5", "49000"), 0, Decimal("0"))
        if request_id in {"fixture:300000", "fixture:600000", "fixture:900000"}:
            raise ConnectionError("fixture network failure")
        return PolicyResponse(_payload(request_id, "set_target", "0.5", "50000"), 1_200_000, Decimal("0"))


def test_pending_safe_close_claims_its_decision_slot_before_a_duplicate_tick() -> None:
    result = _runner(_OpenThenFailuresThenOpen(), max_market_gap_ms=300_000).run(
        [_tick(0, "50000"), _tick(300_000, "50000"), _tick(600_000, "50000"), _tick(900_000, "50000"), _tick(1_200_000, "51000"), _tick(1_200_000, "51000")]
    )

    assert result.records[-1].status == "forced_safe_close"
    assert result.records[-1].decision_id == "fixture:1200000"
    assert result.final_snapshot.signed_quantity == Decimal("0")
