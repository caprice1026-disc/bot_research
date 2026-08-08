from datetime import datetime, timezone

import httpx

from weather_research.collectors.noaa import parse_ncei_observations
from weather_research.collectors.noaa import NceiClient


def test_parse_ncei_global_hourly_temperature_and_report_type() -> None:
    rows = parse_ncei_observations(
        [
            {
                "STATION": "USW00094728",
                "DATE": "2026-01-02T14:00:00",
                "TMP": "278,5",
                "REPORT_TYPE": "FM-15 METAR",
            }
        ],
        station_id="KNYC",
        received_time=datetime(2026, 1, 2, 15, tzinfo=timezone.utc),
    )

    assert rows[0].station_id == "KNYC"
    assert rows[0].temperature_f == 82.04
    assert rows[0].report_type == "FM-15 METAR"
    assert rows[0].received_time.tzinfo == timezone.utc


def test_ncei_client_requests_global_hourly_station() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/access/services/data/v1"
        assert request.url.params["dataset"] == "global-hourly"
        assert request.url.params["stations"] == "72505394728"
        return httpx.Response(
            200,
            json=[
                {
                    "DATE": "2026-01-02T14:00:00",
                    "TMP": "278,5",
                    "REPORT_TYPE": "FM-15 METAR",
                }
            ],
        )

    client = NceiClient(
        base_url="https://ncei.test/access/services/data/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    rows = client.fetch_global_hourly(
        ncei_station_id="72505394728",
        start_date="2026-01-02",
        end_date="2026-01-02",
        station_id="KNYC",
    )

    assert rows[0].temperature_f == 82.04


def test_ncei_client_chunks_long_date_ranges_to_avoid_api_window_limit() -> None:
    requested_ranges: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = request.url.params["startDate"]
        end = request.url.params["endDate"]
        requested_ranges.append((start, end))
        return httpx.Response(
            200,
            json=[{"DATE": f"{start}T00:00:00", "TMP": "278,5", "REPORT_TYPE": "FM-15"}],
        )

    client = NceiClient(
        base_url="https://ncei.test/access/services/data/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    rows = client.fetch_global_hourly(
        ncei_station_id="72505394728",
        start_date="2026-01-01",
        end_date="2026-01-15",
        station_id="KNYC",
    )

    assert requested_ranges == [("2026-01-01", "2026-01-14"), ("2026-01-15", "2026-01-15")]
    assert len(rows) == 2


def test_ncei_client_retries_transient_disconnect() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("temporary disconnect", request=request)
        return httpx.Response(
            200,
            json=[{"DATE": "2026-01-01T00:00:00", "TMP": "278,5", "REPORT_TYPE": "FM-15"}],
        )

    client = NceiClient(
        base_url="https://ncei.test/access/services/data/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_delay_seconds=0,
    )

    rows = client.fetch_global_hourly(
        ncei_station_id="72505394728",
        start_date="2026-01-01",
        end_date="2026-01-01",
        station_id="KNYC",
    )

    assert attempts == 2
    assert len(rows) == 1
