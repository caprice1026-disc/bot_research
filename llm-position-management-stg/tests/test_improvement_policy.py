from copy import deepcopy

import pytest

from position_stg.config import Config
from position_stg.policy import live_review_schema, validate_live_review


def _response():
    long = {"enabled": True, "trend": "hourly_close_vs_sma20", "trigger": "six_bar_breakout",
            "max_spread_bps": 12, "max_target_multiple": "0.5", "rationale": "Buy the breakout",
            "invalidation": "Fall below the range", "reconsider_when": "Hourly trend changes"}
    short = {**long, "enabled": False, "max_target_multiple": "-0.5"}
    return {
        "market_hypothesis": "Observe the breakout",
        "active_rules": [],
        "failure_modes": [],
        "change_reason": "Keep collecting observations",
        "evidence_ids": [],
        "entries": {"long": long, "short": short},
        "rule_change_evidence_slots": {"long": [], "short": []},
        "improvement": {
            "previous": {"status": "supported", "finding": "One feasible order",
                         "limit": "No completed positions"},
            "next": {"hypothesis": "Tighter spreads improve execution", "action": "observe",
                     "metric": "median effective spread", "expected_direction": "decrease",
                     "accept_if": "Median below 12 bps across ten fills",
                     "rollback_if": "Median rises after ten fills",
                     "target": {"side": "none", "field": "none"}},
        },
    }


@pytest.mark.parametrize(("feedback", "expected"), [("no_trial", "baseline"), ("insufficient", "insufficient")])
def test_review_uses_measured_status_when_conclusions_unavailable(feedback, expected):
    raw = _response()
    validated = validate_live_review(raw, Config(), improvement_feedback={"status": feedback})
    assert validated["improvement"]["previous"]["status"] == expected
    assert raw["improvement"]["previous"]["status"] == "supported"


def test_improvement_response_is_required_for_feedback_and_uses_directional_target():
    cfg = Config()
    raw = _response()
    without_improvement = {k: v for k, v in raw.items() if k != "improvement"}
    assert "improvement" not in live_review_schema(cfg)["required"]
    assert "improvement" in live_review_schema(cfg, improvement=True)["required"]
    assert validate_live_review(without_improvement, cfg) == without_improvement
    with pytest.raises(ValueError, match="improvement fields"):
        validate_live_review(without_improvement, cfg, improvement_feedback={"status": "observed"})

    trial = deepcopy(raw)
    trial["improvement"]["next"]["action"] = "test_change"
    with pytest.raises(ValueError, match="one directional field"):
        validate_live_review(trial, cfg)
    trial["improvement"]["next"]["target"] = {"side": "long", "field": "max_spread_bps"}
    assert validate_live_review(trial, cfg) == trial
