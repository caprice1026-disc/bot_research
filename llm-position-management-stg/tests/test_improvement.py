from decimal import Decimal

from position_stg.improvement import evaluate_improvement


def _entries(spread):
    rule = {"enabled": True, "trend": "hourly_close_vs_sma20", "trigger": "six_bar_breakout",
            "max_spread_bps": spread, "max_target_multiple": "0.5"}
    return {"long": rule, "short": {**rule, "enabled": False, "max_target_multiple": "-0.5"}}


def _trial(previous_entries=_entries(12)):
    return {"version": 2, "activated_slot": 100, "activated_at": 105,
            "previous_entries": previous_entries, "entries": _entries(16),
            "plan": {"hypothesis": "A wider spread cap permits more displayed executable entries."}}


def _sample(slot, spread):
    opportunity = {"trends": ["hourly_close_vs_sma20"], "triggers": ["six_bar_breakout"],
                   "deltas": {"0.5": "2.5"}}
    return {"scheduled_at": slot, "spread_bps": str(spread), "long": opportunity, "short": opportunity}


def _fill(stamp, start, side, size, pnl="0", fee_currency="USDC"):
    return {"at": stamp, "raw": {"startPosition": start, "side": side, "sz": size,
                                 "closedPnl": pnl, "fee": "0.1", "feeToken": fee_currency}}


def test_trial_feedback_only_uses_forward_observations_and_accounts_for_cash_flows():
    fills = [_fill(103, "1", "A", "1", "999"),
             _fill(110, "0", "B", "1"),
             _fill(115, "1", "A", "0.4", "3"),
             _fill(120, "0.6", "A", "0.6", "4"),
             _fill(121, "0", "A", "1"),
             _fill(125, "-1", "B", "1", "-1"),
             _fill(130, "0", "A", "1", "-999")]
    funding = [{"at": 100, "raw": {"delta": {"usdc": "999"}}},
               {"at": 112, "raw": {"delta": {"usdc": "-0.2"}}}]
    evidence = {"since": 90, "until": 130, "fills": fills, "funding": funding,
                "history_complete": True, "opening_position": "0"}
    samples = [_sample(99, 14), _sample(100, 14), _sample(110, 10), _sample(120, 17), _sample(130, 14)]
    report = evaluate_improvement(_trial(), samples, evidence, until_slot=130)
    assert report["status"] == report["observed"]["status"] == report["feasibility"]["status"] == "observed"
    assert report["window"] == {"execution_since": 105, "execution_until": 130,
                                "first_feasibility_slot": 100, "feasibility_until_slot": 130}
    assert report["applied_changes"]["long"] == {"max_spread_bps": {"before": 12, "after": 16}}
    assert report["plan"]["hypothesis"].startswith("A wider spread")
    assert report["feasibility"]["examples"] == 3
    assert report["feasibility"]["long"] == {
        "active_feasible": 2, "prior_feasible": 1, "added": 1, "removed": 0, "resized": 0}
    assert report["observed"]["fills"] == 5
    assert report["observed"]["closing_fills"] == 3
    assert report["observed"]["closed_episodes"] == report["observed"]["completed_episodes"] == 2
    assert Decimal(report["observed"]["net_cash_flow_usdc"]) == Decimal("5.3")


def test_baseline_carry_in_close_is_not_a_completed_trial_episode():
    fills = [_fill(110, "1", "A", "1", "2")]
    evidence = {"since": 105, "until": 130, "fills": fills, "funding": [],
                "history_complete": True, "opening_position": "1"}
    report = evaluate_improvement(_trial(None), [_sample(110, 14)], evidence, until_slot=130)
    assert report["status"] == "no_trial"
    assert report["observed"]["status"] == "insufficient"
    assert report["feasibility"]["status"] == "baseline"
    assert report["observed"]["closed_episodes"] == 1
    assert report["observed"]["completed_episodes"] == 0
    assert Decimal(report["observed"]["net_cash_flow_usdc"]) == Decimal("1.9")


def test_unreconciled_fill_reports_unknown_net_and_episode_counts():
    fills = [_fill(110, "1", "A", "1", "2")]
    evidence = {"since": 105, "until": 130, "fills": fills, "funding": [],
                "history_complete": True, "opening_position": "0"}
    report = evaluate_improvement(_trial(), [], evidence, until_slot=130)["observed"]
    assert report["status"] == "insufficient"
    assert report["history_observed"] is False
    assert report["net_cash_flow_usdc"] is None
    assert report["closed_episodes"] is None
