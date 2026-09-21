from dataclasses import replace
from decimal import Decimal as D
import pytest
from position_stg.config import Config
from position_stg.domain import account, advance, capacity, enforce, equity, execute, plan, track_risk
from position_stg.fixture import FixtureMarket


def decision(target=None, stop=None):
    return {"intent": "hold" if target is None else "set_target", "target_multiple": target, "stop_price": stop, "reason": "test"}


def test_open_hold_resize_reverse_and_close():
    cfg, a = Config(), account(500)
    m = FixtureMarket(lambda: 300).snapshot()
    events = execute(a, plan(a, m["mark"], m["step"], decision("1", "49000"), cfg), m, 300, cfg)
    assert len(events) == 2 and D(a["quantity"]) > 0
    qty = a["quantity"]
    assert execute(a, plan(a, m["mark"], m["step"], decision(), cfg), m, 301, cfg) == []
    assert a["quantity"] == qty
    wider = plan(a, m["mark"], m["step"], decision(None, "45000"), cfg)
    events = execute(a, wider, m, 302, cfg)
    assert events[0]["kind"] == "fill" and events[-1]["kind"] == "stop"
    assert abs(D(a["quantity"])) < D(qty)
    events = execute(a, plan(a, m["mark"], m["step"], decision("-1", "51000"), cfg), m, 303, cfg)
    fills = [e for e in events if e["kind"] == "fill"]
    assert len(fills) == 2 and fills[0]["reason"] == "reversal_close"
    assert D(a["quantity"]) < 0
    execute(a, plan(a, m["mark"], m["step"], decision("0"), cfg), m, 304, cfg)
    assert D(a["quantity"]) == 0 and a["stop"] is None


def test_dd_resume_keeps_global_peak_and_maximum():
    cfg, a = Config(), account(500)
    a["cash"] = "450"
    track_risk(a, 50000, 100, cfg)
    assert a["dd_stopped_at"] == 100
    a["quantity"] = ".001"
    a["entry"] = "50000"
    track_risk(a, 50000, 90000, cfg)
    assert a["dd_stopped_at"] == 100
    a["quantity"] = "0"
    track_risk(a, 50000, 90001, cfg)
    assert a["dd_stopped_at"] is None
    assert a["segment_peak"] == "450" and a["peak"] == "500"
    assert D(a["max_dd"]) == D(".1")


def test_price_drift_blocks_add_and_reversal_open_but_allows_exit():
    cfg, a = Config(), account(500)
    m = FixtureMarket(lambda: 300).snapshot()
    execute(a, plan(a, 50000, m["step"], decision("1", "49000"), cfg), m, 300, cfg)
    p = plan(a, 50200, m["step"], decision("2", "49000"), cfg, observed_mark=50000)
    assert p["target"] == a["quantity"]
    p = plan(a, 50200, m["step"], decision("-1", "51000"), cfg, observed_mark=50000)
    assert p["target"] == "0"


def test_risk_reduction_ignores_rebalance_threshold():
    cfg = replace(Config(), rebalance_threshold=D(".99"))
    a = account(500)
    a.update(quantity=".03", entry="50000", stop="49000")
    m = FixtureMarket(lambda: 300).snapshot()
    assert enforce(a, m, 300, cfg)
    assert abs(D(a["quantity"])) * D(1000) < equity(a, 50000) * cfg.stop_risk


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", True])
def test_reject_nonfinite(value):
    with pytest.raises(ValueError):
        plan(account(500), 50000, ".00001", decision("1", value), Config())


def test_gap_blocks_claiming_complete_result():
    a = account(500)
    assert advance(a, [], [], 300, 600, Config())[0]["kind"] == "gap"
    assert a["partial"] and a["gap_since"] == 300


def test_stop_gap_uses_worse_open_price_and_funding_once():
    a = account(500)
    a.update(quantity=".01", entry="50000", stop="49000")
    bars = [{"t": 3600000, "o": "48000", "h": "48500", "l": "47500", "c": "48000"}]
    events = advance(a, bars, [{"time": 3610000, "fundingRate": ".001"}], 3600, 3660, Config())
    fill = next(e for e in events if e["kind"] == "fill")
    assert D(fill["price"]) < 48000 and D(a["quantity"]) == 0
    assert D(a["funding"]) > 0


def test_half_step_and_config_validation():
    with pytest.raises(ValueError):
        plan(account(500), 50000, ".00001", decision("1.1", "49000"), Config())
    with pytest.raises(ValueError):
        Config.from_env({"DRAWDOWN_LIMIT": "0.2"})
    cfg = Config.from_env({"LLM_BUDGET_ACTION": "stop", "REVIEWER_MODEL": "other"})
    assert cfg.llm_budget_action == "stop" and cfg.reviewer_model == "other"


def test_intraminute_stop_widening_cannot_erase_old_trigger():
    a = account(500)
    a.update(quantity="1", entry="100", stop="99", opened_at=200)
    market = {"mark": "100", "step": ".01", "asks": [{"px": "100", "sz": "100"}], "bids": [{"px": "100", "sz": "100"}]}
    execute(a, {"target": "1", "stop": "98", "reason": "edit"}, market, 330, Config())
    events = advance(a, [{"t": 300000, "o": "100", "h": "101", "l": "98.5", "c": "100"}], [], 300, 360, Config())
    assert any(e["kind"] == "ambiguous_stop_edit" for e in events)
    assert a["partial"] and D(a["quantity"]) == 0


def test_intraminute_stop_tightening_marks_new_stop_hit_ambiguous():
    a = account(500)
    a.update(quantity="1", entry="100", stop="98", opened_at=200)
    market = {"mark": "100", "step": ".01", "asks": [{"px": "100", "sz": "100"}], "bids": [{"px": "100", "sz": "100"}]}
    execute(a, {"target": "1", "stop": "99", "reason": "edit"}, market, 330, Config())
    events = advance(a, [{"t": 300000, "o": "100", "h": "101", "l": "98.5", "c": "100"}], [], 300, 360, Config())
    assert a["partial"] and any(e["kind"] == "ambiguous_stop_edit" for e in events)
