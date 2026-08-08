import pytest

from weather_research.rules import InvalidMarketRule, parse_market_rule


def test_parse_market_rule_preserves_station_date_and_bucket_boundaries() -> None:
    payload = {
        "id": "market-1",
        "eventId": "event-1",
        "question": "New York high temperature on 2026-01-02",
        "description": "Resolution uses the official station and daily maximum.",
        "clobTokenIds": '["yes-token", "no-token"]',
        "weather_rule": {
            "station_id": "KNYC",
            "target_date": "2026-01-02",
            "timezone": "America/New_York",
            "lower_bound_f": 81,
            "upper_bound_f": 82,
            "lower_inclusive": True,
            "upper_inclusive": True,
            "resolution_source": "NWS",
        },
    }

    rule = parse_market_rule(payload)

    assert rule.market_id == "market-1"
    assert rule.event_id == "event-1"
    assert rule.station_id == "KNYC"
    assert rule.target_date.isoformat() == "2026-01-02"
    assert rule.lower_bound_f == 81
    assert rule.upper_bound_f == 82
    assert rule.yes_token_id == "yes-token"


def test_parse_market_rule_rejects_missing_resolution_fields() -> None:
    with pytest.raises(InvalidMarketRule, match="station_id"):
        parse_market_rule(
            {
                "id": "market-2",
                "question": "ambiguous",
                "weather_rule": {"target_date": "2026-01-02"},
            }
        )


def test_parse_realistic_polymarket_temperature_question_without_llm() -> None:
    rule = parse_market_rule(
        {
            "id": "1103787",
            "question": "Will the highest temperature in New York City be between 36-37°F on January 6?",
            "description": "The resolution source uses the LaGuardia Airport Station: https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA. It measures whole degrees Fahrenheit.",
            "resolutionSource": "",
            "endDate": "2026-01-06T12:00:00Z",
            "clobTokenIds": '["yes-token", "no-token"]',
        }
    )

    assert rule.station_id == "KLGA"
    assert rule.target_date.isoformat() == "2026-01-06"
    assert rule.lower_bound_f == 36
    assert rule.upper_bound_f == 37
    assert rule.lower_inclusive is True
    assert rule.upper_inclusive is True
