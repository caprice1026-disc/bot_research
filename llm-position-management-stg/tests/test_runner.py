from copy import deepcopy
import pytest
from position_stg.config import Config
from position_stg.config import dec
from position_stg.fixture import FixtureMarket, FixturePolicy, run_fixture
from position_stg.runner import Runner
from position_stg.store import SQLiteStore, Lease, Conflict
from position_stg.app import make_app


def setup(tmp_path):
    clock = [1800000000.0]
    store = SQLiteStore(tmp_path / "state.db")
    market = FixtureMarket(lambda: clock[0])
    policy = FixturePolicy()
    runner = Runner(Config(), store, market, policy, news=lambda *a, **k: [], clock=lambda: clock[0])
    return clock, store, market, policy, runner


def test_fixture_restart_idempotence(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    result = run_fixture(Config(), store)
    assert result["status"] == "fixture_complete"
    assert all(c["status"] == "complete" for c in result["cycles"])
    assert result["accounts"]["llm"]["fills"] >= 4


def test_model_migration_preserves_position_and_starts_new_evaluation_segment(tmp_path):
    from dataclasses import replace
    from decimal import Decimal
    clock, store, market, policy, runner = setup(tmp_path)
    old_cfg = replace(Config(), trader_model="gemini-3.5-flash",
                      trader_output_usd_per_million=Decimal("9"))
    runner.cfg = old_cfg
    runner.run(clock[0])
    clock[0] += 300
    runner.run(clock[0])
    before = deepcopy(store.read("runtime")[0]["accounts"]["llm"])
    assert dec(before["quantity"]) > 0
    runner.cfg = Config()
    policy.target = "hold"
    clock[0] += 300
    runner.run(clock[0])
    state = store.read("runtime")[0]
    for key in ("cash", "quantity", "entry", "stop", "fills"):
        assert state["accounts"]["llm"][key] == before[key]
    assert state["config_hash"] == Config().fingerprint()
    assert state["evaluation_segment_started_at"] == clock[0]
    assert state["config_changes"] >= 1


def test_lease_fencing_and_admin_conflict(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    clock = [100]
    old = Lease(store, {"x": 0}, clock=lambda: clock[0], duration=10)
    with pytest.raises(Conflict):
        Lease(store, {}, clock=lambda: clock[0])
    clock[0] = 111
    new = Lease(store, {}, clock=lambda: clock[0])
    with pytest.raises(Conflict):
        old.save()
    with pytest.raises(Conflict):
        old.release()
    new.release()


def test_first_review_then_trader_and_late_slot(tmp_path):
    clock, store, market, policy, runner = setup(tmp_path)
    assert runner.run(clock[0])["review_version"] == 1
    assert [role for role, _ in policy.calls] == ["reviewer"]
    clock[0] += 300
    runner.run(clock[0])
    assert policy.calls[-1][0] == "trader"
    count = len(policy.calls)
    clock[0] += 361
    runner.run(clock[0] - 61)
    assert len(policy.calls) == count


def test_llm_failure_preserves_protection(tmp_path):
    clock, store, market, policy, runner = setup(tmp_path)
    runner.run(clock[0])
    clock[0] += 300
    runner.run(clock[0])
    a = deepcopy(store.read("runtime")[0]["accounts"]["llm"])
    def fail(*args):
        raise TimeoutError()
    policy.call = fail
    clock[0] += 300
    result = runner.run(clock[0])
    assert result["decision"] == "TimeoutError"
    current = store.read("runtime")[0]["accounts"]["llm"]
    assert current["quantity"] == a["quantity"] and current["stop"] == a["stop"]


def test_crash_after_claim_does_not_recall_policy(tmp_path):
    clock, store, market, policy, runner = setup(tmp_path)
    runner.run(clock[0])
    clock[0] += 300
    market.snapshot = lambda: (_ for _ in ()).throw(ConnectionError())
    assert runner.run(clock[0])["status"] == "error"
    assert runner.run(clock[0])["status"] == "duplicate"
    assert len(policy.calls) == 1


def test_stop_during_llm_wait_invalidates_response(tmp_path):
    clock, store, market, policy, runner = setup(tmp_path)
    runner.run(clock[0])
    clock[0] += 300
    runner.run(clock[0])
    original = policy.call
    def delayed(role, request, inputs):
        result = original(role, request, inputs)
        market.mark = 48000
        return result
    policy.call = delayed
    clock[0] += 300
    result = runner.run(clock[0])
    assert result["decision"] == "stale_position"
    assert dec(store.read("runtime")[0]["accounts"]["llm"]["quantity"]) == 0
    trend = store.read("runtime")[0]["accounts"]["trend"]
    assert dec(trend["entry"]) < 48100
    assert store.read("runtime")[0]["last_mark"] == "48000"


def test_http_rejects_missing_or_naive_schedule(tmp_path):
    *_, runner = setup(tmp_path)
    app = make_app(runner)
    for value in ("", "2026-01-01T00:00:00"):
        status = []
        app({"PATH_INFO": "/tick", "REQUEST_METHOD": "POST", "HTTP_X_CLOUDSCHEDULER_SCHEDULETIME": value}, lambda s, h: status.append(s))
        assert status == ["400 Bad Request"]


def test_failed_settlement_does_not_partially_charge_funding(tmp_path):
    from position_stg.runner import initial_state
    clock, store, market, policy, runner = setup(tmp_path)
    state = initial_state(Config(), 1800000000)
    state["accounts"]["llm"].update(quantity=".001", entry="50000", stop="49000")
    clock[0] += 300
    before = deepcopy(state)
    original = market.history
    def malformed(since, until):
        bars, _ = original(since, until)
        return bars, [{"time": (since+10)*1000, "fundingRate": ".001"}, {"time": (since+100)*1000, "fundingRate": "NaN"}]
    market.history = malformed
    with pytest.raises(ValueError):
        runner.settle(state, market.snapshot(), {"events": []})
    assert state == before


def test_news_failure_is_distinct_and_does_not_force_liquidation(tmp_path):
    clock, store, market, policy, runner = setup(tmp_path)
    runner.news = lambda *a, **k: (_ for _ in ()).throw(ConnectionError())
    result = runner.run(clock[0])
    assert result["review_version"] == 1
    done = [v for k, v, _ in store.items("cycle-") if k.endswith("-done")][0]
    assert done["news_error"] == "ConnectionError"
