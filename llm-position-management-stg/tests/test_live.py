from copy import deepcopy
from dataclasses import replace
import threading
import time
import pytest

from position_stg.config import Config
from position_stg.live import LiveRunner, summary
from position_stg.store import SQLiteStore
from position_stg.entries import evaluate_entries, permits_increase
from position_stg.policy import validate_live_review
from position_stg.domain import parse_decision


class FakeExchange:
    def __init__(self):
        self.quantity = "0"
        self.fill_rows, self.funding_rows = [], []
        self.funding_error = False

    def snapshot(self):
        return {"quantity": self.quantity, "entry": "100", "equity": "500",
                "mark": "100", "step": ".01", "orders": []}

    def fills(self, since):
        return deepcopy([row for row in self.fill_rows if row["time"] / 1000 >= since])

    def funding(self, since):
        if self.funding_error:
            raise TimeoutError()
        return deepcopy([row for row in self.funding_rows if row["time"] / 1000 >= since])


class FakeController:
    def __init__(self):
        self.exchange = FakeExchange()
        self.maintenance_calls = 0
        self.tick_calls = []
        self.status = "checked"

    def maintain(self):
        self.maintenance_calls += 1
        return {"status": self.status, "snapshot": self.exchange.snapshot(),
                "manual_pause": self.status == "paused"}

    def tick(self, raw, slot, **kwargs):
        self.tick_calls.append((deepcopy(raw), slot, kwargs))
        if raw["intent"] == "set_target":
            self.exchange.quantity = raw["target_multiple"]
        return {"status": "checked", "snapshot": self.exchange.snapshot(), "plan": raw}


class FakeMarket:
    def __init__(self, clock):
        self.clock = clock

    def snapshot(self):
        return {"mark": "100", "at": self.clock(), "step": ".01",
                "bids": [{"px": "99.99"}], "asks": [{"px": "100.01"}]}

    def features(self, now):
        bars = [{"c": "99", "h": "99.5", "l": "98"} for _ in range(59)]
        bars.append({"c": "100", "h": "100", "l": "99"})
        return {"5m": {"bars": bars, "sma20": "99.05"},
                "1h": {"bars": [{"c": "101"}], "sma20": "100", "sma50": "99"}}


def entry_plans():
    return {side: {"enabled": True, "trend": "hourly_close_vs_sma20", "trigger": "six_bar_breakout",
                   "max_spread_bps": 10, "max_target_multiple": cap, "rationale": "follow the break",
                   "invalidation": "close back in prior range", "reconsider_when": "hourly trend changes"}
            for side, cap in (("long", "1.0"), ("short", "-1.0"))}


class FakePolicy:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock
        self.calls = []
        self.fail = None
        self.target = "hold"
        self.delay = 0
        self.hook = None

    def call(self, role, request_id, inputs):
        self.calls.append((role, request_id, deepcopy(inputs)))
        if self.delay and role == "trader":
            time.sleep(self.delay)
        if self.hook:
            self.hook(role)
        if role == self.fail:
            self.store.write("llm-" + request_id, {"status": "error", "estimated_usd": ".01", "at": self.clock()}, 0)
            raise TimeoutError()
        self.store.write("llm-" + request_id, {"status": "received", "role": role,
                         "estimated_usd": ".001", "input_tokens": 10, "output_tokens": 5,
                         "at": self.clock()}, 0)
        if role == "reviewer":
            return {"market_hypothesis": "test", "active_rules": ["wait"], "failure_modes": [],
                    "change_reason": "initial", "evidence_ids": [], "entries": entry_plans()}
        return {"intent": "hold" if self.target == "hold" else "set_target",
                "target_multiple": None if self.target == "hold" else self.target,
                "stop_price": None if self.target in {"hold", "0"} else "99", "reason": "retain position"}


def setup(tmp_path, **config):
    clock = [1800000000.0]
    cfg = replace(Config(bot_id="live-testnet"), **config)
    store = SQLiteStore(tmp_path / "live.db")
    controller = FakeController()
    policy = FakePolicy(store, lambda: clock[0])
    market = FakeMarket(lambda: clock[0])
    runner = LiveRunner(cfg, store, market, policy, controller, clock=lambda: clock[0])
    return clock, store, controller, policy, runner


def test_continuous_hold_and_same_slot_restart_never_resubmit(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    policy.target = "1"
    assert runner.run(clock[0])["status"] == "complete"
    assert [role for role, _, _ in policy.calls] == ["reviewer", "trader"]
    assert policy.calls[0][2]["news"] == []
    assert policy.calls[0][2]["news_status"] == "disabled"
    clock[0] += 300
    policy.target = "hold"
    assert runner.run(clock[0])["status"] == "complete"
    assert ctl.exchange.quantity == "1"
    count = len(policy.calls)
    restarted = LiveRunner(runner.cfg, store, runner.market, policy, ctl, clock=lambda: clock[0])
    assert restarted.run(clock[0])["status"] == "duplicate"
    assert len(policy.calls) == count and len(ctl.tick_calls) == 2
    assert ctl.tick_calls[-1][2] == {"allow_increase": True, "observed_mark": "100", "expected_quantity": "1"}
    assert summary(store)["recent_decisions"][-1]["reason"] == "retain position"


def test_interrupted_slot_and_existing_llm_reservation_are_not_replayed(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    store.write("live-cycle-1800000000", {"status": "started", "scheduled_at": int(clock[0])}, 0)
    assert runner.run(clock[0])["previous_status"] == "started"
    assert policy.calls == [] and ctl.tick_calls == []
    clock[0] += 300
    store.write("llm-live-reviewer-1800000300", {"status": "unknown", "estimated_usd": ".1"}, 0)
    assert runner.run(clock[0])["status"] == "error"
    assert policy.calls == []


def test_review_failure_preserves_protection_and_does_not_retry_every_slot(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    policy.fail = "reviewer"
    assert runner.run(clock[0])["status"] == "error"
    initial_maintenance = ctl.maintenance_calls
    clock[0] += 300
    assert runner.run(clock[0])["decision_status"] == "strategy_expired_or_missing"
    assert len(policy.calls) == 1 and ctl.tick_calls == []
    assert ctl.maintenance_calls > initial_maintenance
    clock[0] = 1800007200
    policy.fail = None
    assert runner.run(clock[0])["status"] == "complete"
    assert [role for role, _, _ in policy.calls] == ["reviewer", "reviewer", "trader"]


def test_review_interval_expired_strategy_and_late_start(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    runner.run(clock[0])
    clock[0] += 6900
    runner.run(clock[0])
    assert sum(role == "reviewer" for role, _, _ in policy.calls) == 1
    clock[0] += 300
    runner.run(clock[0])
    assert sum(role == "reviewer" for role, _, _ in policy.calls) == 2
    state, version = store.read("live-runtime")
    clock[0] += 300
    state.update(last_review=clock[0] - 21601, last_review_attempt=clock[0])
    store.write("live-runtime", state, version)
    calls = len(policy.calls)
    assert runner.run(clock[0])["decision_status"] == "strategy_expired_or_missing"
    assert len(policy.calls) == calls
    clock[0] += 361
    assert runner.run(clock[0] - 61)["decision_status"] == "late_start"
    assert len(policy.calls) == calls


def test_llm_wait_keeps_maintaining_and_failure_never_executes(tmp_path, monkeypatch):
    clock, store, ctl, policy, runner = setup(tmp_path)
    monkeypatch.setattr("position_stg.live.PROTECTION_SECONDS", .01)
    policy.delay, policy.fail = .06, "trader"
    result = runner.run(clock[0])
    assert result["status"] == "error" and result["decision_status"] == "trader_failed"
    assert ctl.maintenance_calls >= 4 and ctl.tick_calls == []
    report = summary(store)
    assert report["api_usage_known_calls"] == 1 and report["api_unknown_calls"] == 1
    assert report["api_unknown_reserved_usd"] == "0.01"


def test_summary_deduplicates_real_events_and_exposes_missing_history(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    ctl.exchange.fill_rows = [{"time": int(clock[0] * 1000), "coin": "BTC", "tid": 1,
                               "oid": 2, "closedPnl": "2", "fee": ".05", "feeToken": "USDC"}]
    ctl.exchange.funding_rows = [{"time": int(clock[0] * 1000), "hash": "funding-1",
                                  "delta": {"type": "funding", "coin": "BTC", "usdc": "-.1"}}]
    runner.run(clock[0])
    runner.maintain()
    report = summary(store)
    assert report["observed_fills"] == report["observed_funding_events"] == 1
    assert report["observed_realized_net_usdc"] == "1.85"
    assert report["api_estimated_known_usd"] == "0.002"
    assert report["profitability_evidence"] is False
    ctl.exchange.funding_error = True
    runner.maintain()
    report = summary(store)
    assert report["accounting_status"] == "partial_or_unavailable"
    assert report["observed_realized_net_usdc"] is None
    assert report["history"]["funding"]["error_type"] == "TimeoutError"
    clock[0] += 300
    result = runner.run(clock[0])
    assert result["status"] == "partial"
    assert result["errors"][-1]["phase"] == "funding_history"


def test_unknown_position_and_stop_request_suppress_models_and_orders(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    ctl.status = "paused"
    assert runner.run(clock[0])["status"] == "blocked"
    assert policy.calls == [] and ctl.tick_calls == []
    clock[0] += 300
    ctl.status = "checked"
    store.write("live-control", {"stop_requested": True}, 0)
    assert runner.run(clock[0])["decision_status"] == "stop_requested"
    assert policy.calls == []
    runner.run_forever(max_cycles=1)
    assert policy.calls == []


def test_stop_arriving_during_response_prevents_order(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    def stop(role):
        if role == "trader":
            store.write("live-control", {"stop_requested": True}, 0)
    policy.hook = stop
    result = runner.run(clock[0])
    assert result["status"] == "error" and ctl.tick_calls == []
    assert "trader_response" in result


def test_forever_failure_has_backoff_and_can_stop(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    policy.fail = "reviewer"
    class Stop:
        stopped = False
        waits = []
        def is_set(self):
            return self.stopped
        def wait(self, seconds):
            self.waits.append(seconds)
            self.stopped = True
    stop = Stop()
    report = runner.run_forever(stop_event=stop)
    assert stop.waits == [15] and report["cycles"] == 1
    assert ctl.tick_calls == []


def test_bounded_loop_outputs_cycle_and_returns(tmp_path, capsys):
    clock, store, ctl, policy, runner = setup(tmp_path)
    report = runner.run_forever(max_cycles=1)
    assert report["complete_cycles"] == 1
    assert '"event": "testnet_cycle"' in capsys.readouterr().out


def test_reviewer_gets_twenty_four_decisions_and_actual_execution_evidence(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    runner.run(clock[0])
    for i in range(1, 24):
        clock[0] = 1800000000 + 300 * i
        runner.run(clock[0])
    clock[0] += 300
    ctl.exchange.fill_rows = [{"time": int(clock[0] * 1000), "coin": "BTC", "tid": 9,
                               "oid": 2, "closedPnl": "2", "fee": ".05", "feeToken": "USDC"}]
    runner.run(clock[0])
    inputs = [inputs for role, _, inputs in policy.calls if role == "reviewer"][-1]
    assert len(inputs["recent_decisions"]) == 24
    assert inputs["recent_decisions"][-1]["execution"]["status"] == "checked"
    assert inputs["recent_decisions"][-1]["account_after"]["equity"] == "500"
    assert inputs["execution_evidence"]["window_fill_count"] == 1
    assert inputs["execution_evidence"]["cumulative_observed"]["observed_fees_usdc"] == "0.05"
    counts = inputs["entry_summary"]
    assert counts["slots"] == counts["assessed"] == counts["long"]["eligible_but_held"] == 24
    assert counts["short"]["failed_trend"] == counts["short"]["failed_trigger"] == 24
    assert inputs["entry_contract"]["multiples"]["short"][0] == "-0.5"
    assert summary(store)["recent_decisions"][-1]["entry_checks"]["long"]["eligible"]


def test_legacy_strategy_reviewed_before_trading_and_entry_gate_controls_increases(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    runner._state()
    runner._update(strategy={"version": 4, "active_rules": ["wait"]}, strategy_version=4,
                   last_review=clock[0]-300, last_review_attempt=clock[0]-300,
                   recent_decisions=[{"scheduled_at": clock[0]-300, "reason": "legacy hold"}])
    policy.target = "-0.5"
    result = runner.run(clock[0])
    assert result["review"]["version"] == 5 and result["status"] == "complete"
    assert policy.calls[0][2]["entry_summary"]["unassessed"] == 1
    assert not result["entry_allows_increase"] and not ctl.tick_calls[-1][2]["allow_increase"]
    assert summary(store)["entry_summary_last_24_cycles"]["short"]["eligible"] == 0
    clock[0] += 300
    policy.target = "2.0"  # Qualified long, but above the selected 1x cap.
    assert runner.run(clock[0])["entry_allows_increase"] is False
    assert not ctl.tick_calls[-1][2]["allow_increase"]


def test_short_retest_spread_and_size_contract(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    plans = entry_plans()
    plan = plans["short"]
    plan.update(trend="any", trigger="sma20_retest", max_target_multiple="-0.5")
    f = runner.market.features(clock[0])
    f["5m"]["bars"][-2]["c"] = "98"
    f["5m"]["bars"][-1] = {"h": "100", "l": "97", "c": "97.5"}
    f["5m"]["sma20"] = "99"
    book = runner.market.snapshot()
    checks = evaluate_entries(plans, f, book)
    short = parse_decision({"intent": "set_target", "target_multiple": "-0.5", "stop_price": "101", "reason": "retest"})
    assert checks["short"]["eligible"] and permits_increase(short, plans, checks)
    book["asks"][0]["px"] = "101"
    checks = evaluate_entries(plans, f, book)
    assert checks["short"]["signal_met"] and checks["short"]["failed"] == ["spread"]
    assert not permits_increase(short, plans, checks)
    assert permits_increase({**short, "target_multiple": 0}, plans, checks)
    review = {"entries": plans, "market_hypothesis": "test", "active_rules": [], "failure_modes": [],
              "change_reason": "test", "evidence_ids": []}
    validate_live_review(review, runner.cfg)
    plan["max_target_multiple"] = "-0.2"
    with pytest.raises(ValueError):
        validate_live_review(review, runner.cfg)


def test_timed_out_worker_never_applies_late_result_or_overlaps_another_call(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path, trader_timeout_seconds=1)
    release = threading.Event()
    policy.hook = lambda role: release.wait(5) if role == "trader" else None
    try:
        result = runner.run(clock[0])
        assert result["status"] == "error" and result["errors"][0]["error_type"] == "TimeoutError"
        calls = len(policy.calls)
        clock[0] += 300
        assert runner.run(clock[0])["status"] == "error"
        assert len(policy.calls) == calls and ctl.tick_calls == []
    finally:
        release.set()
        runner._pending.join(timeout=2)
    assert not runner._pending.is_alive()
    assert ctl.tick_calls == []


def test_failed_new_review_blocks_old_strategy_until_successful_review(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    runner.run(clock[0])
    clock[0] += 7200
    policy.fail = "reviewer"
    assert runner.run(clock[0])["status"] == "error"
    calls, orders = len(policy.calls), len(ctl.tick_calls)
    clock[0] += 300
    assert runner.run(clock[0])["decision_status"] == "strategy_expired_or_missing"
    assert len(policy.calls) == calls and len(ctl.tick_calls) == orders


def history_row(kind, at, identity):
    if kind == "fill":
        return {"time": int(at * 1000), "coin": "BTC", "tid": identity, "oid": identity,
                "closedPnl": "1", "fee": ".01", "feeToken": "USDC"}
    return {"time": int(at * 1000), "hash": str(identity),
            "delta": {"type": "funding", "coin": "BTC", "usdc": "-.01"}}


def test_empty_response_and_event_watermark_preserve_delayed_fill_and_funding(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    start = clock[0]
    runner.maintain()
    clock[0] += 15
    runner.maintain()  # Events at +10 seconds have not been published yet.
    state, _ = store.read("live-runtime")
    assert state["fill_cursor"] == state["funding_cursor"] == start
    clock[0] += 15
    ctl.exchange.fill_rows = [history_row("fill", start + 10, 1)]
    ctl.exchange.funding_rows = [history_row("funding", start + 10, 1)]
    runner.maintain()
    state, _ = store.read("live-runtime")
    assert state["fill_cursor"] == state["funding_cursor"] == start + 10
    assert summary(store)["observed_fills"] == summary(store)["observed_funding_events"] == 1


def test_overlap_recovers_older_event_after_later_one_and_restart(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    start = clock[0]
    runner.maintain()
    clock[0] += 120
    ctl.exchange.fill_rows = [history_row("fill", start + 110, 2)]
    ctl.exchange.funding_rows = [history_row("funding", start + 110, 2)]
    runner.maintain()
    clock[0] += 15
    ctl.exchange.fill_rows.append(history_row("fill", start + 90, 1))
    ctl.exchange.funding_rows.append(history_row("funding", start + 90, 1))
    restarted = LiveRunner(runner.cfg, store, runner.market, policy, ctl, clock=lambda: clock[0])
    restarted.maintain()
    restarted.maintain()
    state, _ = store.read("live-runtime")
    assert state["fill_cursor"] == state["funding_cursor"] == start + 110
    assert summary(store)["observed_fills"] == summary(store)["observed_funding_events"] == 2


def test_periodic_full_rescan_recovers_publication_older_than_overlap(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    start = clock[0]
    runner.maintain()
    clock[0] += 9000
    ctl.exchange.fill_rows = [history_row("fill", start + 8990, 2)]
    ctl.exchange.funding_rows = [history_row("funding", start + 8990, 2)]
    runner.maintain()  # Full reconciliation at +9000; old event is still unpublished.
    clock[0] += 15
    ctl.exchange.fill_rows.append(history_row("fill", start + 10, 1))
    ctl.exchange.funding_rows.append(history_row("funding", start + 10, 1))
    runner.maintain()  # Normal overlap deliberately excludes the very old event.
    assert summary(store)["observed_fills"] == 1
    clock[0] = start + 16200
    runner.maintain()
    assert summary(store)["observed_fills"] == summary(store)["observed_funding_events"] == 2
    assert summary(store)["history"]["fill"]["full_rescan"] is True


def test_saturated_history_stays_partial_and_cannot_advance_cursor(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    start = clock[0]
    runner.maintain()
    clock[0] += 9000
    # Duplicate response rows still indicate a possible endpoint limit; no optimistic count.
    ctl.exchange.fill_rows = [history_row("fill", start + 8990, 1)] * 500
    ctl.exchange.funding_rows = [history_row("funding", start + 8990, 1)] * 500
    runner.maintain()
    state, _ = store.read("live-runtime")
    assert state["fill_cursor"] == state["funding_cursor"] == start
    assert summary(store)["accounting_status"] == "partial_or_unavailable"
    ctl.exchange.fill_rows = [history_row("fill", start + 8990, 1)]
    ctl.exchange.funding_rows = [history_row("funding", start + 8990, 1)]
    clock[0] += 15
    runner.maintain()
    # A subsequent narrow success does not erase the unresolved full-history scan.
    assert summary(store)["history"]["fill"]["status"] == "reconciliation_incomplete"
    clock[0] = start + 16200
    runner.maintain()
    assert summary(store)["accounting_status"] == "observed_only"


def test_old_wall_clock_cursor_is_reset_before_history_upgrade(tmp_path):
    clock, store, ctl, policy, runner = setup(tmp_path)
    start = clock[0]
    runner.maintain()
    state, version = store.read("live-runtime")
    state.pop("history_cursor_schema")
    state.update(fill_cursor=start + 90000, funding_cursor=start + 90000)
    store.write("live-runtime", state, version)
    clock[0] += 30
    ctl.exchange.fill_rows = [history_row("fill", start + 10, 1)]
    ctl.exchange.funding_rows = [history_row("funding", start + 10, 1)]
    runner.maintain()
    state, _ = store.read("live-runtime")
    assert state["history_cursor_schema"] == 2
    assert state["fill_cursor"] == state["funding_cursor"] == start + 10
    assert summary(store)["observed_fills"] == summary(store)["observed_funding_events"] == 1
