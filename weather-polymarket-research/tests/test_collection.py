from weather_research.collection import (
    build_dataset_manifest,
    filter_market_rules_by_target_date,
    observation_coverage_reason,
    select_new_york_markets,
)
from weather_research.config import NEW_YORK
from weather_research.schemas import OutcomeStatus


def test_select_new_york_markets_uses_question_and_description() -> None:
    markets = [
        {"id": "m1", "question": "New York high temperature", "description": "weather"},
        {"id": "m2", "question": "Chicago high temperature", "description": "weather"},
        {"id": "m3", "question": "New York election", "description": "politics"},
    ]

    assert select_new_york_markets(markets) == [markets[0]]


def test_filter_market_rules_by_target_date_keeps_inclusive_window() -> None:
    rules = [
        {"market_id": "before", "target_date": "2025-08-08"},
        {"market_id": "first", "target_date": "2025-08-09"},
        {"market_id": "last", "target_date": "2026-08-09"},
        {"market_id": "after", "target_date": "2026-08-10"},
    ]

    filtered = filter_market_rules_by_target_date(rules, "2025-08-09", "2026-08-09")

    assert [rule["market_id"] for rule in filtered] == ["first", "last"]


def test_observation_coverage_reason_reports_returned_range() -> None:
    observations = [
        {"observation_time": "2025-08-09T00:51:00Z"},
        {"observation_time": "2025-08-27T03:51:00Z"},
    ]

    assert observation_coverage_reason(observations) == (
        "NCEI observations cover 2025-08-09T00:51:00Z through 2025-08-27T03:51:00Z"
    )


def test_new_york_config_matches_polymarket_resolution_station() -> None:
    assert NEW_YORK.station_id == "KLGA"
    assert NEW_YORK.ncei_station_id == "72503014732"


def test_manifest_reports_insufficient_data_when_gefs_history_is_missing() -> None:
    manifest = build_dataset_manifest(
        city="new-york",
        station_id="KNYC",
        source_results={
            "gamma": {"status": "ok"},
            "ncei": {"status": "ok"},
            "gefs": {"status": "ok"},
        },
        counts={"markets_raw": 10, "market_rules": 0, "observations": 100, "forecast_members": 0},
        reasons=["no Point-in-Time GEFS forecast members were normalized"],
    )

    assert manifest["status"] == OutcomeStatus.INSUFFICIENT_DATA.value
    assert manifest["counts"]["observations"] == 100
    assert manifest["reasons"] == ["no Point-in-Time GEFS forecast members were normalized"]
