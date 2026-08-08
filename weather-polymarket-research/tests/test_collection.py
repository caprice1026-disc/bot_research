from datetime import datetime, timezone

from weather_research.collection import (
    build_dataset_manifest,
    collect_gefs_target_day,
    collect_polymarket_target_day_prices,
    filter_market_rules_by_target_date,
    observation_coverage_reason,
    select_new_york_markets,
)
from weather_research.config import NEW_YORK
from weather_research.schemas import ForecastMember
from weather_research.schemas import PricePoint
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


def test_collect_gefs_target_day_persists_forecast_members(monkeypatch, tmp_path) -> None:
    class FakeGefsGribClient:
        def fetch_target_day_members(self, **kwargs):
            return [
                ForecastMember(
                    station_id="KLGA",
                    forecast_issue_time=datetime(2026, 1, 5, 12, tzinfo=timezone.utc),
                    forecast_valid_time=datetime(2026, 1, 7, 5, tzinfo=timezone.utc),
                    received_time=datetime(2026, 1, 5, 12, tzinfo=timezone.utc),
                    ensemble_member=0,
                    temperature_f=40.0,
                )
            ]

    monkeypatch.setattr("weather_research.collection.GefsGribClient", FakeGefsGribClient)

    manifest = collect_gefs_target_day(
        output_dir=tmp_path / "data",
        target_date="2026-01-06",
        issue_time="2026-01-05T12:00:00Z",
        max_members=1,
    )

    assert manifest["status"] == "success"
    assert manifest["forecast_members"] == 1
    assert (tmp_path / "data" / "normalized" / "forecast_members.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert (tmp_path / "results" / "gefs_forecast_manifest.json").exists()


def test_collect_polymarket_target_day_prices_persists_price_points(monkeypatch, tmp_path) -> None:
    class FakePolymarketClient:
        def fetch_price_history(self, market_id, token_id, start_ts, end_ts):
            return [
                PricePoint(
                    market_id=market_id,
                    token_id=token_id,
                    timestamp=datetime(2026, 1, 5, 12, 0, 7, tzinfo=timezone.utc),
                    price=0.4,
                )
            ]

    rules_path = tmp_path / "data" / "normalized"
    rules_path.mkdir(parents=True)
    (rules_path / "market_rules.jsonl").write_text(
        '{"market_id":"m1","target_date":"2026-01-06","yes_token_id":"token-1"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("weather_research.collection.PolymarketClient", FakePolymarketClient)

    manifest = collect_polymarket_target_day_prices(
        output_dir=tmp_path / "data",
        target_date="2026-01-06",
        start_time="2026-01-05T12:00:00Z",
        end_time="2026-01-07T05:00:00Z",
    )

    assert manifest["status"] == "success"
    assert manifest["price_points"] == 1
    assert (tmp_path / "data" / "normalized" / "price_points.jsonl").read_text(encoding="utf-8").count("\n") == 1
    assert (tmp_path / "results" / "price_history_manifest.json").exists()


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
