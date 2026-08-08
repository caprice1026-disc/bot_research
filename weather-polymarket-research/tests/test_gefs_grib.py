import httpx
import pytest
from datetime import date, datetime, timezone
from eccodes import codes_get_message, codes_grib_new_from_samples, codes_release, codes_set, codes_set_values

from weather_research.collectors.gefs_grib import (
    GefsGribClient,
    find_message_range,
    build_gefs_tmax_key,
    kelvin_to_fahrenheit,
    parse_grib_index,
    select_index_entry,
    target_day_member_from_points,
    tmax_steps_for_target_day,
)


def test_parse_grib_index_selects_two_meter_tmax_and_next_message_boundary() -> None:
    index = "\n".join(
        [
            "63:11908835:d=2026010600:TMP:2 m above ground:3 hour fcst:ENS=+1",
            "64:12151280:d=2026010600:RH:2 m above ground:3 hour fcst:ENS=+1",
            "65:12368642:d=2026010600:TMAX:2 m above ground:0-3 hour max fcst:ENS=+1",
            "66:12608464:d=2026010600:TMIN:2 m above ground:0-3 hour min fcst:ENS=+1",
        ]
    )

    entries = parse_grib_index(index)
    selected = select_index_entry(entries, short_name="TMAX", level="2 m above ground")

    assert selected.number == 65
    assert selected.offset == 12368642
    assert find_message_range(entries, selected) == (12368642, 12608463)


def test_find_message_range_uses_open_end_for_last_message() -> None:
    entries = parse_grib_index("1:0:d=2026010600:TMP:2 m above ground:anl:ENS=+1")

    assert find_message_range(entries, entries[0]) == (0, None)


def test_kelvin_to_fahrenheit_converts_absolute_temperature() -> None:
    assert kelvin_to_fahrenheit(273.15) == 32.0


def test_gefs_grib_client_uses_index_range_for_selected_message() -> None:
    index = "\n".join(
        [
            "1:0:d=2026010600:TMP:2 m above ground:3 hour fcst:ENS=+1",
            "2:100:d=2026010600:TMAX:2 m above ground:0-3 hour max fcst:ENS=+1",
            "3:200:d=2026010600:TMIN:2 m above ground:0-3 hour min fcst:ENS=+1",
        ]
    )
    calls: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("Range")))
        if request.url.path.endswith(".idx"):
            return httpx.Response(200, text=index)
        return httpx.Response(206, content=b"selected-grib-message")

    client = GefsGribClient(
        base_url="https://noaa-gefs.test/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    payload = client.fetch_tmax_message("gefs.20260106/00/member.f003")

    assert payload == b"selected-grib-message"
    assert calls == [
        ("/gefs.20260106/00/member.f003.idx", None),
        ("/gefs.20260106/00/member.f003", "bytes=100-199"),
    ]


def test_gefs_grib_client_reuses_cached_index_and_message(tmp_path) -> None:
    index = "1:0:d=2026010600:TMP:2 m above ground:3 hour fcst:ENS=+1\n2:100:d=2026010600:TMAX:2 m above ground:0-3 hour max fcst:ENS=+1\n3:200:d=2026010600:TMIN:2 m above ground:0-3 hour min fcst:ENS=+1"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith(".idx"):
            return httpx.Response(200, text=index)
        return httpx.Response(206, content=b"selected-grib-message")

    client = GefsGribClient(
        base_url="https://noaa-gefs.test/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_dir=tmp_path / "gefs-cache",
        retry_backoff_seconds=0,
    )

    assert client.fetch_tmax_message("gefs.20260106/00/member.f003") == b"selected-grib-message"
    assert client.fetch_tmax_message("gefs.20260106/00/member.f003") == b"selected-grib-message"
    assert calls == [
        "/gefs.20260106/00/member.f003.idx",
        "/gefs.20260106/00/member.f003",
    ]


def test_gefs_grib_client_retries_transient_http_failures() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, text="temporary")
        return httpx.Response(200, text="ok")

    client = GefsGribClient(
        base_url="https://noaa-gefs.test/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_backoff_seconds=0,
    )

    assert client._get("catalog") .text == "ok"
    assert attempts == 3


def test_decode_grib_point_reads_temperature_and_forecast_times() -> None:
    from weather_research.collectors.gefs_grib import decode_grib_point

    handle = codes_grib_new_from_samples("regular_ll_sfc_grib2")
    for key, value in [
        ("shortName", "tmax"),
        ("typeOfLevel", "heightAboveGround"),
        ("level", 2),
        ("dataDate", 20260106),
        ("dataTime", 0),
        ("endStep", 3),
    ]:
        codes_set(handle, key, value)
    codes_set_values(handle, [273.15] * 496)
    payload = codes_get_message(handle)
    codes_release(handle)

    decoded = decode_grib_point(payload, latitude=40.0, longitude=10.0)

    assert decoded["temperature_f"] == pytest.approx(32.0, abs=0.001)
    assert decoded["issue_time"].isoformat() == "2026-01-06T00:00:00+00:00"
    assert decoded["valid_time"].isoformat() == "2026-01-06T03:00:00+00:00"
    assert decoded["ensemble_member"] == 0


def test_tmax_interval_overlap_includes_partial_boundary_intervals() -> None:
    from weather_research.collectors.gefs_grib import tmax_interval_overlaps

    target_start = datetime(2026, 1, 6, 5, tzinfo=timezone.utc)
    target_end = datetime(2026, 1, 7, 5, tzinfo=timezone.utc)

    assert tmax_interval_overlaps(
        datetime(2026, 1, 7, 3, tzinfo=timezone.utc),
        datetime(2026, 1, 7, 6, tzinfo=timezone.utc),
        target_start,
        target_end,
    )
    assert not tmax_interval_overlaps(
        datetime(2026, 1, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 6, 3, tzinfo=timezone.utc),
        target_start,
        target_end,
    )


def test_tmax_steps_cover_local_day_with_dst_aware_boundaries() -> None:
    issue_time = datetime(2026, 7, 4, 12, tzinfo=timezone.utc)

    assert tmax_steps_for_target_day(issue_time, date(2026, 7, 5), "America/New_York") == [
        18,
        21,
        24,
        27,
        30,
        33,
        36,
        39,
        42,
    ]


def test_build_gefs_tmax_key_uses_control_and_perturbation_names() -> None:
    issue_time = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)

    assert build_gefs_tmax_key(issue_time, ensemble_member=0, forecast_hour=18).endswith(
        "/gec00.t12z.pgrb2a.0p50.f018"
    )
    assert build_gefs_tmax_key(issue_time, ensemble_member=7, forecast_hour=18).endswith(
        "/gep07.t12z.pgrb2a.0p50.f018"
    )


def test_target_day_member_uses_maximum_eligible_tmax_and_preserves_pit_time() -> None:
    issue_time = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
    points = [
        {
            "temperature_f": 40.0,
            "issue_time": issue_time,
            "valid_time": datetime(2026, 1, 6, 6, tzinfo=timezone.utc),
            "interval_start": datetime(2026, 1, 6, 3, tzinfo=timezone.utc),
            "interval_end": datetime(2026, 1, 6, 6, tzinfo=timezone.utc),
            "ensemble_member": 1,
        },
        {
            "temperature_f": 45.0,
            "issue_time": issue_time,
            "valid_time": datetime(2026, 1, 7, 3, tzinfo=timezone.utc),
            "interval_start": datetime(2026, 1, 7, 0, tzinfo=timezone.utc),
            "interval_end": datetime(2026, 1, 7, 3, tzinfo=timezone.utc),
            "ensemble_member": 1,
        },
    ]

    member = target_day_member_from_points(points, "KLGA", date(2026, 1, 6), "America/New_York")

    assert member is not None
    assert member.temperature_f == 45.0
    assert member.forecast_issue_time == issue_time
    assert member.received_time == issue_time
    assert member.forecast_valid_time == datetime(2026, 1, 7, 5, tzinfo=timezone.utc)


def test_gefs_grib_client_collects_target_day_members(monkeypatch: pytest.MonkeyPatch) -> None:
    import weather_research.collectors.gefs_grib as module

    issue_time = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
    calls: list[str] = []
    monkeypatch.setattr(module, "tmax_steps_for_target_day", lambda *_: [18])

    client = module.GefsGribClient(
        base_url="https://noaa-gefs.test/",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )

    def fetch(key: str) -> bytes:
        calls.append(key)
        return key.encode()

    def decode(payload: bytes, latitude: float, longitude: float) -> dict[str, object]:
        key = payload.decode()
        member = 0 if "/gec00." in key else 1
        return {
            "temperature_f": 40.0 + member,
            "issue_time": issue_time,
            "valid_time": datetime(2026, 1, 6, 6, tzinfo=timezone.utc),
            "interval_start": datetime(2026, 1, 6, 3, tzinfo=timezone.utc),
            "interval_end": datetime(2026, 1, 6, 6, tzinfo=timezone.utc),
            "ensemble_member": member,
        }

    monkeypatch.setattr(client, "fetch_tmax_message", fetch)
    monkeypatch.setattr(module, "decode_grib_point", decode)

    members = client.fetch_target_day_members(
        issue_time=issue_time,
        target_date=date(2026, 1, 6),
        station_id="KLGA",
        latitude=40.77945,
        longitude=-73.88027,
        timezone_name="America/New_York",
        ensemble_members=[0, 1],
    )

    assert [member.ensemble_member for member in members] == [0, 1]
    assert [member.temperature_f for member in members] == [40.0, 41.0]
    assert len(calls) == 2
