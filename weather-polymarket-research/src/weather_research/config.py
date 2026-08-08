"""Research constants and the explicit New York station mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CityConfig:
    slug: str
    station_id: str
    ncei_station_id: str
    timezone: str
    latitude: float
    longitude: float


NEW_YORK = CityConfig(
    slug="new-york",
    station_id="KLGA",
    ncei_station_id="72503014732",
    timezone="America/New_York",
    latitude=40.77945,
    longitude=-73.88027,
)


SOURCE_ENDPOINTS = {
    "gamma": "https://gamma-api.polymarket.com/markets?limit=1&offset=0",
    "clob": "https://clob.polymarket.com/time",
    "gefs": "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.&delimiter=/",
    "ncei": "https://www.ncei.noaa.gov/access/services/data/v1?dataset=global-hourly&stations=72503014732&startDate=2025-08-09&endDate=2026-08-09&format=json&units=standard&limit=1",
}
