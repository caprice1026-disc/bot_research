from decimal import Decimal

from llm_position_management.scripted_replay import LifecyclePolicy


def test_script_does_not_reopen_after_stop_between_scheduled_entries():
    policy = LifecyclePolicy(0, 86_400_000)
    observation = {"as_of_ms": 900_000, "mark_price": "100",
                   "position": {"signed_quantity": "0", "stop_price": None}}
    response = policy.decide(observation, "run:900000")
    assert response.payload["intent"] == "hold"
    assert response.estimated_cost_usd == Decimal("0")


def test_script_closes_at_terminal_slot_and_has_no_future_input():
    policy = LifecyclePolicy(0, 86_400_000)
    observation = {"as_of_ms": 86_100_000, "mark_price": "100",
                   "position": {"signed_quantity": "1", "stop_price": "98"}}
    response = policy.decide(observation, "run:86100000")
    assert response.payload["target_fraction"] == "0"
    assert response.payload["stop_price"] is None
