from datetime import date, datetime, timezone

import pytest

from weather_research.candidate_builder import build_pit_candidates
from weather_research.schemas import ForecastMember, MarketRule, Observation, OutcomeStatus, PricePoint


UTC = timezone.utc


def _rule() -> MarketRule:
    return MarketRule(
        market_id="m1",
        question="Will the high be 36-37F?",
        description="KLGA",
        station_id="KLGA",
        target_date=date(2026, 1, 6),
        timezone="America/New_York",
        lower_bound_f=36,
        upper_bound_f=37,
        resolution_source="https://example.test/rule",
        yes_token_id="yes-1",
    )


def _forecast(member: int, temperature: float, received_hour: int = 12) -> ForecastMember:
    return ForecastMember(
        station_id="KLGA",
        forecast_issue_time=datetime(2026, 1, 5, 12, tzinfo=UTC),
        forecast_valid_time=datetime(2026, 1, 7, 5, tzinfo=UTC),
        received_time=datetime(2026, 1, 5, received_hour, tzinfo=UTC),
        ensemble_member=member,
        temperature_f=temperature,
    )


def _observation() -> Observation:
    return Observation(
        station_id="KLGA",
        observation_time=datetime(2026, 1, 6, 18, tzinfo=UTC),
        received_time=datetime(2026, 1, 8, tzinfo=UTC),
        temperature_f=36.6,
    )


def test_build_pit_candidates_joins_probabilities_observation_and_historical_price() -> None:
    result = build_pit_candidates(
        [_rule()],
        [_forecast(0, 36.0), _forecast(1, 37.0), _forecast(2, 38.0)],
        [_observation()],
        [
            PricePoint(
                market_id="m1",
                token_id="yes-1",
                timestamp=datetime(2026, 1, 5, 13, tzinfo=UTC),
                price=0.30,
            )
        ],
    )

    assert result.status is OutcomeStatus.SUCCESS
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.model_probability == pytest.approx(2 / 3)
    assert candidate.ensemble_probability == pytest.approx(2 / 3)
    assert 0 < candidate.gaussian_probability < 1
    assert candidate.observed_ask == pytest.approx(0.30)
    assert candidate.observed_mid == pytest.approx(0.30)
    assert candidate.price_source == "historical_price_proxy"
    assert candidate.outcome_yes is True


def test_build_pit_candidates_uses_best_ask_for_level_two_and_does_not_add_spread_twice() -> None:
    result = build_pit_candidates(
        [_rule()],
        [_forecast(0, 36.0)],
        [_observation()],
        [
            PricePoint(
                market_id="m1",
                token_id="yes-1",
                timestamp=datetime(2026, 1, 5, 13, tzinfo=UTC),
                price=0.32,
                best_bid=0.30,
                best_ask=0.34,
            )
        ],
        require_best_ask=True,
    )

    assert result.status is OutcomeStatus.SUCCESS
    candidate = result.candidates[0]
    assert candidate.observed_ask == pytest.approx(0.34)
    assert candidate.observed_mid == pytest.approx(0.32)
    assert candidate.observed_spread == pytest.approx(0.0)
    assert candidate.price_source == "clob_best_ask"


def test_build_pit_candidates_blocks_when_observation_period_is_missing() -> None:
    result = build_pit_candidates(
        [_rule()],
        [_forecast(0, 36.0)],
        [],
        [
            PricePoint(
                market_id="m1",
                token_id="yes-1",
                timestamp=datetime(2026, 1, 5, 13, tzinfo=UTC),
                price=0.30,
            )
        ],
    )

    assert result.status is OutcomeStatus.INSUFFICIENT_DATA
    assert result.candidates == []
    assert any("observation" in reason.lower() for reason in result.reasons)


def test_build_pit_candidates_drops_forecasts_not_available_at_price_time() -> None:
    result = build_pit_candidates(
        [_rule()],
        [_forecast(0, 36.0, received_hour=14)],
        [_observation()],
        [
            PricePoint(
                market_id="m1",
                token_id="yes-1",
                timestamp=datetime(2026, 1, 5, 13, tzinfo=UTC),
                price=0.30,
            )
        ],
    )

    assert result.status is OutcomeStatus.INSUFFICIENT_DATA
    assert result.candidates == []
    assert any("Point-in-Time" in reason for reason in result.reasons)
