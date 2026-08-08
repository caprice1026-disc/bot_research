from datetime import datetime, timezone

from weather_research.backtest import BacktestCandidate, ExecutionConfig, run_backtest
from weather_research.reporting import validate_results, write_backtest_artifacts


def test_write_and_validate_backtest_artifacts(tmp_path) -> None:
    result = run_backtest(
        [
            BacktestCandidate(
                market_id="m1",
                trade_time=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
                forecast_issue_time=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
                received_time=datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
                model_probability=0.65,
                observed_ask=0.40,
                outcome_yes=True,
                quantity=1.0,
            )
        ],
        ExecutionConfig(),
    )

    write_backtest_artifacts(result, tmp_path, tmp_path / "report.md")

    validation = validate_results(tmp_path)
    assert validation == {"valid": True, "status": "success", "trade_count": 1}
    assert "Net PnL" in (tmp_path / "report.md").read_text(encoding="utf-8")
