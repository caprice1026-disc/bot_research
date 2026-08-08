from datetime import datetime, timezone

import pytest

from weather_research.backtest import BacktestCandidate, ExecutionConfig, run_backtest
from weather_research.schemas import OutcomeStatus


UTC = timezone.utc


def _candidate(received_time: datetime) -> BacktestCandidate:
    return BacktestCandidate(
        market_id="m1",
        trade_time=datetime(2026, 1, 1, 12, tzinfo=UTC),
        forecast_issue_time=datetime(2026, 1, 1, 8, tzinfo=UTC),
        received_time=received_time,
        model_probability=0.65,
        observed_ask=0.40,
        outcome_yes=True,
        quantity=1.0,
    )


def test_run_backtest_records_level_two_execution_and_pnl() -> None:
    result = run_backtest([_candidate(datetime(2026, 1, 1, 9, tzinfo=UTC))], ExecutionConfig())

    assert result.status is OutcomeStatus.SUCCESS
    assert len(result.trades) == 1
    assert result.trades[0].executable_price == pytest.approx(0.42)
    assert result.trades[0].gross_pnl == pytest.approx(0.60)
    assert result.trades[0].net_pnl == pytest.approx(0.57)


def test_run_backtest_marks_future_forecast_as_insufficient_data() -> None:
    result = run_backtest([_candidate(datetime(2026, 1, 1, 12, 1, tzinfo=UTC))], ExecutionConfig())

    assert result.status is OutcomeStatus.INSUFFICIENT_DATA
    assert result.trades == []
    assert "Point-in-Time" in result.reason
