"""GRIB2 index parsing and message selection helpers for GEFS."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from collections.abc import Iterable
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..schemas import ForecastMember


@dataclass(frozen=True)
class GribIndexEntry:
    number: int
    offset: int
    descriptor: str


class GefsGribError(RuntimeError):
    """Raised when a GEFS GRIB2 message cannot be retrieved or decoded."""


def parse_grib_index(index_text: str) -> list[GribIndexEntry]:
    entries: list[GribIndexEntry] = []
    for line_number, line in enumerate(index_text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"invalid GRIB index line {line_number}")
        try:
            number = int(parts[0])
            offset = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"invalid GRIB index offset on line {line_number}") from exc
        entries.append(GribIndexEntry(number=number, offset=offset, descriptor=parts[2]))
    if not entries:
        raise ValueError("GRIB index is empty")
    return entries


def select_index_entry(
    entries: list[GribIndexEntry],
    short_name: str,
    level: str,
) -> GribIndexEntry:
    needle = f":{short_name}:{level}:"
    for entry in entries:
        if needle in f":{entry.descriptor}":
            return entry
    raise LookupError(f"GRIB index has no {short_name} message at {level}")


def find_message_range(entries: list[GribIndexEntry], selected: GribIndexEntry) -> tuple[int, int | None]:
    try:
        position = entries.index(selected)
    except ValueError as exc:
        raise ValueError("selected GRIB entry is not part of the index") from exc
    next_offset = entries[position + 1].offset if position + 1 < len(entries) else None
    return selected.offset, next_offset - 1 if next_offset is not None else None


def kelvin_to_fahrenheit(value: float) -> float:
    return (float(value) - 273.15) * 9.0 / 5.0 + 32.0


def tmax_interval_overlaps(
    interval_start: datetime,
    interval_end: datetime,
    target_start: datetime,
    target_end: datetime,
) -> bool:
    return interval_start < target_end and interval_end > target_start


def _tmax_interval_for_step(issue_time: datetime, step: int) -> tuple[datetime, datetime]:
    if step <= 0 or step % 3:
        raise ValueError("GEFS TMAX step must be a positive multiple of 3")
    if step in (3, 6):
        start_step = 0
    elif step % 6 == 3:
        start_step = step - 3
    else:
        start_step = step - 6
    return issue_time + timedelta(hours=start_step), issue_time + timedelta(hours=step)


def tmax_steps_for_target_day(issue_time: datetime, target_date: date, timezone_name: str) -> list[int]:
    target_start = datetime.combine(target_date, time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
    target_end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(
        timezone.utc
    )
    return [
        step
        for step in range(3, 73, 3)
        if tmax_interval_overlaps(*_tmax_interval_for_step(issue_time, step), target_start, target_end)
    ]


def build_gefs_tmax_key(issue_time: datetime, ensemble_member: int, forecast_hour: int) -> str:
    if issue_time.tzinfo is None or issue_time.utcoffset() is None:
        raise ValueError("issue_time must be timezone-aware")
    if not 0 <= ensemble_member <= 20:
        raise ValueError("ensemble_member must be between 0 and 20")
    if forecast_hour <= 0 or forecast_hour % 3:
        raise ValueError("forecast_hour must be a positive multiple of 3")
    member_name = "gec00" if ensemble_member == 0 else f"gep{ensemble_member:02d}"
    issue_utc = issue_time.astimezone(timezone.utc)
    date_part = issue_utc.strftime("%Y%m%d")
    cycle = issue_utc.strftime("%H")
    return (
        f"gefs.{date_part}/{cycle}/atmos/pgrb2ap5/"
        f"{member_name}.t{cycle}z.pgrb2a.0p50.f{forecast_hour:03d}"
    )


def target_day_member_from_points(
    points: list[dict[str, Any]],
    station_id: str,
    target_date: date,
    timezone_name: str,
) -> ForecastMember | None:
    if not points:
        return None
    issue_time = max(point["issue_time"] for point in points)
    target_start = datetime.combine(target_date, time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
    target_end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(
        timezone.utc
    )
    eligible = [
        point
        for point in points
        if tmax_interval_overlaps(point["interval_start"], point["interval_end"], target_start, target_end)
    ]
    if not eligible:
        return None
    selected = max(eligible, key=lambda point: point["temperature_f"])
    return ForecastMember(
        station_id=station_id,
        forecast_issue_time=issue_time,
        forecast_valid_time=target_end,
        received_time=issue_time,
        ensemble_member=int(selected["ensemble_member"]),
        temperature_f=float(selected["temperature_f"]),
    )


class GefsGribClient:
    def __init__(
        self,
        base_url: str = "https://noaa-gefs-pds.s3.amazonaws.com/",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=60.0)

    def _get(self, key: str, headers: dict[str, str] | None = None) -> httpx.Response:
        response = self.client.get(f"{self.base_url}/{key.lstrip('/')}", headers=headers)
        if response.status_code >= 400:
            raise GefsGribError(f"GEFS HTTP {response.status_code}: {response.text[:200]}")
        return response

    def fetch_tmax_message(self, key: str) -> bytes:
        index_response = self._get(f"{key}.idx")
        entries = parse_grib_index(index_response.text)
        selected = select_index_entry(entries, short_name="TMAX", level="2 m above ground")
        start, end = find_message_range(entries, selected)
        if end is None:
            raise GefsGribError("TMAX message is the last GRIB message; object size is required")
        response = self._get(key, headers={"Range": f"bytes={start}-{end}"})
        if response.status_code != 206:
            raise GefsGribError("GEFS object server did not honor the requested byte range")
        return response.content

    def fetch_target_day_members(
        self,
        issue_time: datetime,
        target_date: date,
        station_id: str,
        latitude: float,
        longitude: float,
        timezone_name: str,
        ensemble_members: Iterable[int] = range(21),
    ) -> list[ForecastMember]:
        forecasts: list[ForecastMember] = []
        steps = tmax_steps_for_target_day(issue_time, target_date, timezone_name)
        for ensemble_member in ensemble_members:
            points: list[dict[str, Any]] = []
            for step in steps:
                key = build_gefs_tmax_key(issue_time, ensemble_member, step)
                payload = self.fetch_tmax_message(key)
                points.append(decode_grib_point(payload, latitude, longitude))
            member = target_day_member_from_points(points, station_id, target_date, timezone_name)
            if member is not None:
                forecasts.append(member)
        return forecasts


def decode_grib_point(
    payload: bytes,
    latitude: float,
    longitude: float,
    expected_short_name: str = "tmax",
) -> dict[str, Any]:
    """Decode one single-message GRIB2 payload at the nearest grid point."""
    try:
        from eccodes import (
            codes_get,
            codes_grib_find_nearest,
            codes_is_defined,
            codes_new_from_message,
            codes_release,
        )
    except ImportError as exc:
        raise GefsGribError("eccodes is required for GEFS GRIB2 decoding") from exc

    handle = codes_new_from_message(payload)
    if handle is None:
        raise GefsGribError("GRIB2 payload contained no message")
    try:
        short_name = str(codes_get(handle, "shortName"))
        if short_name != expected_short_name:
            raise GefsGribError(f"expected {expected_short_name}, got {short_name}")
        nearest = codes_grib_find_nearest(handle, latitude, longitude, npoints=1)[0]
        data_date = int(codes_get(handle, "dataDate"))
        data_time = int(codes_get(handle, "dataTime"))
        issue_time = datetime(
            data_date // 10000,
            (data_date // 100) % 100,
            data_date % 100,
            data_time // 100,
            data_time % 100,
            tzinfo=timezone.utc,
        )
        start_step = int(codes_get(handle, "startStep")) if codes_is_defined(handle, "startStep") else 0
        end_step = int(codes_get(handle, "endStep"))
        member = int(codes_get(handle, "perturbationNumber")) if codes_is_defined(handle, "perturbationNumber") else 0
        interval_start = issue_time + timedelta(hours=start_step)
        interval_end = issue_time + timedelta(hours=end_step)
        return {
            "short_name": short_name,
            "temperature_f": kelvin_to_fahrenheit(float(nearest["value"])),
            "issue_time": issue_time,
            "valid_time": interval_end,
            "interval_start": interval_start,
            "interval_end": interval_end,
            "ensemble_member": member,
            "grid_latitude": float(nearest["lat"]),
            "grid_longitude": float(nearest["lon"]),
            "distance": float(nearest["distance"]),
        }
    finally:
        codes_release(handle)
