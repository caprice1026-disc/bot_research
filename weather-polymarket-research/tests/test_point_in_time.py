from datetime import datetime, timezone

from weather_research.pit import filter_available_records, is_available_at_trade_time
from weather_research.schemas import ForecastMember, PricePoint


UTC = timezone.utc


def test_forecast_received_before_trade_is_available() -> None:
    trade_time = datetime(2026, 1, 1, 12, tzinfo=UTC)
    forecast = ForecastMember(
        station_id="KNYC",
        forecast_issue_time=datetime(2026, 1, 1, 8, tzinfo=UTC),
        forecast_valid_time=datetime(2026, 1, 2, 0, tzinfo=UTC),
        received_time=datetime(2026, 1, 1, 9, tzinfo=UTC),
        ensemble_member=0,
        temperature_f=82.0,
    )

    assert is_available_at_trade_time(forecast, trade_time)


def test_forecast_received_after_trade_is_excluded_even_if_issue_is_early() -> None:
    trade_time = datetime(2026, 1, 1, 12, tzinfo=UTC)
    forecast = ForecastMember(
        station_id="KNYC",
        forecast_issue_time=datetime(2026, 1, 1, 8, tzinfo=UTC),
        forecast_valid_time=datetime(2026, 1, 2, 0, tzinfo=UTC),
        received_time=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
        ensemble_member=0,
        temperature_f=82.0,
    )

    assert not is_available_at_trade_time(forecast, trade_time)


def test_price_points_after_trade_are_excluded_from_batch() -> None:
    trade_time = datetime(2026, 1, 1, 12, tzinfo=UTC)
    before = PricePoint(
        market_id="m1",
        token_id="t1",
        timestamp=datetime(2026, 1, 1, 11, tzinfo=UTC),
        best_bid=0.38,
        best_ask=0.40,
    )
    after = PricePoint(
        market_id="m1",
        token_id="t1",
        timestamp=datetime(2026, 1, 1, 13, tzinfo=UTC),
        best_bid=0.50,
        best_ask=0.52,
    )

    assert filter_available_records([before, after], trade_time) == [before]
