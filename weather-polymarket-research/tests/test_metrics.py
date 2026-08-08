import pytest

from weather_research.metrics import brier_score, log_loss, maximum_drawdown


def test_probability_metrics_match_binary_outcomes() -> None:
    assert brier_score([0.8, 0.2], [True, False]) == pytest.approx(0.04)
    assert log_loss([0.8, 0.2], [True, False]) == pytest.approx(-2 * __import__("math").log(0.8) / 2)


def test_maximum_drawdown_uses_peak_to_subsequent_trough() -> None:
    assert maximum_drawdown([10.0, 8.0, 12.0, 7.0, 9.0]) == pytest.approx(5.0)
