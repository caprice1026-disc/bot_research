import json
from datetime import datetime, timezone

from weather_research.backtest import ExecutionConfig, run_backtest_from_artifacts
from weather_research.schemas import ForecastMember, MarketRule, Observation, PricePoint


UTC = timezone.utc


def test_run_backtest_from_artifacts_runs_level_one_only_with_aligned_periods(tmp_path) -> None:
    data_dir = tmp_path / "data"
    normalized = data_dir / "normalized"
    normalized.mkdir(parents=True)
    rule = MarketRule(
        market_id="m1",
        question="Will the high be 36-37F?",
        description="KLGA",
        station_id="KLGA",
        target_date="2026-01-06",
        timezone="America/New_York",
        lower_bound_f=36,
        upper_bound_f=37,
        resolution_source="https://example.test/rule",
        yes_token_id="yes-1",
    )
    forecast = ForecastMember(
        station_id="KLGA",
        forecast_issue_time=datetime(2026, 1, 5, 12, tzinfo=UTC),
        forecast_valid_time=datetime(2026, 1, 7, 5, tzinfo=UTC),
        received_time=datetime(2026, 1, 5, 12, tzinfo=UTC),
        ensemble_member=0,
        temperature_f=36,
    )
    observation = Observation(
        station_id="KLGA",
        observation_time=datetime(2026, 1, 6, 18, tzinfo=UTC),
        received_time=datetime(2026, 1, 8, tzinfo=UTC),
        temperature_f=36,
    )
    price = PricePoint(
        market_id="m1",
        token_id="yes-1",
        timestamp=datetime(2026, 1, 5, 13, tzinfo=UTC),
        price=0.10,
    )
    for filename, rows in (
        ("market_rules.jsonl", [rule.model_dump(mode="json")]),
        ("forecast_members.jsonl", [forecast.model_dump(mode="json")]),
        ("observations.jsonl", [observation.model_dump(mode="json")]),
        ("price_points.jsonl", [price.model_dump(mode="json")]),
    ):
        (normalized / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    result, build = run_backtest_from_artifacts(data_dir, ExecutionConfig(minimum_edge=0.1), level=1)

    assert result.status.value == "success"
    assert len(result.trades) == 1
    assert build.status.value == "success"


def test_run_backtest_from_artifacts_keeps_level_two_insufficient_without_historical_ask(tmp_path) -> None:
    data_dir = tmp_path / "data"
    normalized = data_dir / "normalized"
    normalized.mkdir(parents=True)
    rows = {
        "market_rules.jsonl": [{
            "market_id": "m1", "question": "q", "description": "d", "station_id": "KLGA",
            "target_date": "2026-01-06", "timezone": "America/New_York", "lower_bound_f": 36,
            "upper_bound_f": 37, "resolution_source": "https://example.test/rule", "yes_token_id": "yes-1",
        }],
        "forecast_members.jsonl": [ForecastMember(
            station_id="KLGA", forecast_issue_time=datetime(2026, 1, 5, 12, tzinfo=UTC),
            forecast_valid_time=datetime(2026, 1, 7, 5, tzinfo=UTC), received_time=datetime(2026, 1, 5, 12, tzinfo=UTC),
            ensemble_member=0, temperature_f=36,
        ).model_dump(mode="json")],
        "observations.jsonl": [Observation(
            station_id="KLGA", observation_time=datetime(2026, 1, 6, 18, tzinfo=UTC),
            received_time=datetime(2026, 1, 8, tzinfo=UTC), temperature_f=36,
        ).model_dump(mode="json")],
        "price_points.jsonl": [PricePoint(
            market_id="m1", token_id="yes-1", timestamp=datetime(2026, 1, 5, 13, tzinfo=UTC), price=0.10,
        ).model_dump(mode="json")],
    }
    for filename, values in rows.items():
        (normalized / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in values),
            encoding="utf-8",
        )

    result, build = run_backtest_from_artifacts(data_dir, ExecutionConfig(minimum_edge=0.1), level=2)

    assert result.status.value == "insufficient_data"
    assert result.trades == []
    assert build.status.value == "insufficient_data"
    assert "best ask" in result.reason
