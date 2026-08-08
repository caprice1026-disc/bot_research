import pytest

from weather_research.execution import executable_yes_price, net_edge


def test_executable_yes_price_adds_spread_and_slippage() -> None:
    assert executable_yes_price(0.40, spread=0.01, slippage=0.02) == pytest.approx(0.43)


def test_net_edge_deducts_execution_costs_and_uncertainty() -> None:
    assert net_edge(
        model_probability=0.52,
        observed_ask=0.40,
        fee=0.01,
        spread=0.01,
        slippage=0.02,
        uncertainty_buffer=0.02,
    ) == pytest.approx(0.06)


def test_execution_inputs_cannot_be_outside_probability_range() -> None:
    with pytest.raises(ValueError, match="observed_ask"):
        executable_yes_price(1.01, spread=0.01, slippage=0.01)

    with pytest.raises(ValueError, match="spread"):
        executable_yes_price(0.40, spread=-0.01, slippage=0.01)
