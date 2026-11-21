from copy import deepcopy
from decimal import Decimal

from position_stg.config import Config
from position_stg.review_evidence import apply_review_evidence, build_review_evidence
from position_stg.store import SQLiteStore


def _strategy(spread=12, multiple="0.5"):
    long = {"enabled": True, "trend": "hourly_close_vs_sma20", "trigger": "six_bar_breakout",
            "max_spread_bps": spread, "max_target_multiple": multiple, "rationale": "buy the breakout",
            "invalidation": "fall below the range", "reconsider_when": "hourly trend changes"}
    short = {**long, "enabled": False, "max_target_multiple": "-0.5"}
    return {"entries": {"long": long, "short": short}}


def _historical_cycle():
    bars = [{"c": "99", "h": "99.50", "l": "98"} for _ in range(59)]
    bars.append({"c": "100", "h": "100", "l": "98.50"})
    return {
        "entry_checks": {"historically_assessed": True},
        "before": {"snapshot": {"quantity": "0", "entry": "0", "equity": "500", "available_collateral": "500",
                                "mark": "100", "step": ".01", "orders": []}},
        "features": {"5m": {"bars": bars, "sma20": "99.05", "atr14": "1"},
                     "1h": {"bars": [{"c": "101"}], "sma20": "100", "sma50": "99"}},
        "market_before": {"mark": "100", "bids": [{"px": "99.93", "sz": "3"}],
                          "asks": [{"px": "100.07", "sz": "3"}, {"px": "102", "sz": "100"}]},
    }


def test_review_applies_only_cited_fields_that_change_feasible_orders(tmp_path):
    store = SQLiteStore(tmp_path / "review.db")
    cfg = Config()
    store.write("live-cycle-1800000000", _historical_cycle(), 0)
    examples = build_review_evidence(store, [{"scheduled_at": 1800000000}], cfg)
    assert Decimal(examples[0]["spread_bps"]) == Decimal("14")
    assert examples[0]["long"]["probe"] == "hypothetical_2x_5m_atr"
    assert {k: Decimal(v) for k, v in examples[0]["long"]["deltas"].items()} == {"0.5": Decimal("2.5")}

    previous = _strategy()
    changed = _strategy(spread=16, multiple="1.0")
    changed["rule_change_evidence_slots"] = {"long": [1800000000], "short": []}
    applied, report = apply_review_evidence(changed, previous, examples, cfg)
    assert applied["entries"]["long"]["max_spread_bps"] == 16
    assert applied["entries"]["long"]["max_target_multiple"] == "0.5"
    assert report["long"] == {"accepted": {"max_spread_bps": [1800000000]}, "retained": ["max_target_multiple"]}

    uncited = {**changed, "rule_change_evidence_slots": {"long": [1800000600], "short": []}}
    applied, report = apply_review_evidence(uncited, previous, examples, cfg)
    assert applied["entries"] == previous["entries"]
    assert report["long"]["accepted"] == {}


def test_risk_block_and_ioc_depth_prevent_spread_only_proof(tmp_path):
    store = SQLiteStore(tmp_path / "review.db")
    cfg = Config()
    cycle = _historical_cycle()
    low_depth = deepcopy(cycle)
    low_depth["market_before"]["asks"][0]["sz"] = "0.05"
    store.write("live-cycle-1800000000", low_depth, 0)
    cycle["review_risk_state"] = {"dd_stopped_at": 1799999900}
    store.write("live-cycle-1800000600", cycle, 0)
    recent = [{"scheduled_at": 1800000000}, {"scheduled_at": 1800000600}]
    examples = build_review_evidence(store, recent, cfg)
    assert all(not row["long"]["deltas"] for row in examples)
    changed = _strategy(spread=16)
    changed["rule_change_evidence_slots"] = {"long": [row["scheduled_at"] for row in recent], "short": []}
    previous = _strategy()
    applied, report = apply_review_evidence(changed, previous, examples, cfg)
    assert applied["entries"] == previous["entries"]
    assert report["long"]["retained"] == ["max_spread_bps"]
