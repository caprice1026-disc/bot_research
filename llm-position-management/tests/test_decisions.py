from decimal import Decimal

import pytest

from llm_position_management.decisions import DecisionContext, DecisionError, parse_target_decision


def _context() -> DecisionContext:
    return DecisionContext(decision_id="run:600000", position_version=3)


def test_parser_accepts_only_a_single_matching_target_position_contract() -> None:
    decision = parse_target_decision(
        {
            "schema_version": 1,
            "decision_id": "run:600000",
            "intent": "set_target",
            "target_fraction": "0.50",
            "stop_price": "60000.0",
            "thesis": "confirmed features support a long position",
            "invalidation": "exit if the stop is reached",
        },
        _context(),
    )

    assert decision.target_fraction == Decimal("0.50")
    assert decision.stop_price == Decimal("60000.0")


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "decision_id": "wrong", "intent": "hold", "target_fraction": None, "stop_price": None, "thesis": "x", "invalidation": "x"},
        {"schema_version": 1, "decision_id": "run:600000", "intent": "set_target", "target_fraction": "0.33", "stop_price": "1", "thesis": "x", "invalidation": "x"},
        {"schema_version": 1, "decision_id": "run:600000", "intent": "hold", "target_fraction": "0", "stop_price": None, "thesis": "x", "invalidation": "x"},
        {"schema_version": 1, "decision_id": "run:600000", "intent": "hold", "target_fraction": None, "stop_price": None, "thesis": "x", "invalidation": "x", "unexpected": True},
    ],
)
def test_parser_rejects_id_mismatch_non_discrete_or_non_null_hold(payload: dict[str, object]) -> None:
    with pytest.raises(DecisionError):
        parse_target_decision(payload, _context())
