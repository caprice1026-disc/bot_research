from decimal import Decimal
from llm_position_management.metrics import evaluate
from llm_position_management.policy import ScriptedPolicy
from trading_core.execution.position_plan import ExecutionCosts
from test_runner import _runner, _tick, _payload


def test_metrics_independently_track_drawdown_turnover_and_holding():
    policy = ScriptedPolicy({"fixture:0": _payload("fixture:0", "set_target", "0.5", "49000"),
        "fixture:300000": _payload("fixture:300000", "hold", None, None),
        "fixture:600000": _payload("fixture:600000", "set_target", "0", None)})
    ticks = [_tick(0, "50000"), _tick(300000, "49500"), _tick(600000, "51000")]
    result = _runner(policy).run(ticks)
    report = evaluate(result, ticks, ExecutionCosts(Decimal(0), Decimal(0), Decimal(0)))
    assert report["holding_ms"] == [600000]
    assert Decimal(report["max_drawdown_pct"]) == Decimal("0.125")
    assert Decimal(report["turnover_usd"]) == Decimal("252.5")
    assert Decimal(report["net_pnl"]) == Decimal("2.5")
    assert report["actions"] == {"open":1,"hold":1,"close":1}
