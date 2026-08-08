"""NCEI Global Hourly observation normalization."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import time

from ..schemas import Observation


def _parse_observation_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _temperature_f(row: dict[str, Any]) -> float:
    if row.get("temperature_f") is not None:
        return float(row["temperature_f"])
    raw = str(row.get("TMP", ""))
    value = raw.split(",", 1)[0].strip()
    if not value or value.lstrip("+-").startswith("9999"):
        raise ValueError("missing TMP")
    celsius = float(value) / 10.0
    return celsius * 9.0 / 5.0 + 32.0


def parse_ncei_observations(
    rows: list[dict[str, Any]],
    station_id: str,
    received_time: datetime,
) -> list[Observation]:
    if received_time.tzinfo is None or received_time.utcoffset() is None:
        raise ValueError("received_time must be timezone-aware")
    observations: list[Observation] = []
    for row in rows:
        try:
            observations.append(
                Observation(
                    station_id=station_id,
                    observation_time=_parse_observation_time(str(row["DATE"])),
                    received_time=received_time,
                    temperature_f=_temperature_f(row),
                    report_type=row.get("REPORT_TYPE"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return observations


class NceiClient:
    def __init__(
        self,
        base_url: str = "https://www.ncei.noaa.gov/access/services/data/v1",
        client: httpx.Client | None = None,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=30.0)
        self.retry_delay_seconds = retry_delay_seconds

    def fetch_global_hourly(
        self,
        ncei_station_id: str,
        start_date: str,
        end_date: str,
        station_id: str,
        page_size: int = 1000,
        max_pages: int = 64,
    ) -> list[Observation]:
        received_time = datetime.now(timezone.utc)
        if page_size <= 0 or max_pages <= 0:
            raise ValueError("page_size and max_pages must be positive")
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise ValueError("start_date must not be after end_date")
        observations: list[Observation] = []
        cursor = start
        chunks = 0
        while cursor <= end:
            if chunks >= max_pages:
                raise RuntimeError("NCEI date chunking exceeded max_pages")
            chunk_end = min(cursor + timedelta(days=13), end)
            params = {
                "dataset": "global-hourly",
                "stations": ncei_station_id,
                "startDate": cursor.isoformat(),
                "endDate": chunk_end.isoformat(),
                "format": "json",
                "units": "standard",
                "includeAttributes": "true",
                "limit": page_size,
                "offset": 0,
            }
            response: httpx.Response | None = None
            for attempt in range(3):
                try:
                    response = self.client.get(self.base_url, params=params)
                    if response.status_code < 500:
                        break
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
                if attempt < 2 and self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds * (2**attempt))
            if response is None:
                raise RuntimeError("NCEI request returned no response")
            if response.status_code >= 400:
                raise RuntimeError(f"NCEI HTTP {response.status_code}: {response.text[:200]}")
            try:
                rows = response.json()
            except ValueError as exc:
                raise RuntimeError("NCEI response was not valid JSON") from exc
            if not isinstance(rows, list):
                raise RuntimeError("NCEI response must be a list")
            observations.extend(parse_ncei_observations(rows, station_id, received_time))
            cursor = chunk_end + timedelta(days=1)
            chunks += 1
        deduplicated = {observation.observation_time: observation for observation in observations}
        return [deduplicated[key] for key in sorted(deduplicated)]
