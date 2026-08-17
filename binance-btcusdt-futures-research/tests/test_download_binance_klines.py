from datetime import date, datetime, timezone
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from download_binance_klines import (
    archive_requests,
    archive_url,
    build_direct_opener,
    normalize_and_validate,
)


def utc_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def raw_kline(open_time_ms: int) -> list[str]:
    return [
        str(open_time_ms),
        "100.0",
        "101.0",
        "99.0",
        "100.5",
        "10.0",
        str(open_time_ms + 899_999),
        "1000.0",
        "25",
        "4.0",
        "400.0",
        "0",
    ]


def test_archive_requests_uses_monthly_interior_and_daily_boundary_dates():
    requests = archive_requests(date(2025, 8, 17), date(2026, 8, 17), "1h")

    daily = [request.date_or_month for request in requests if request.kind == "daily"]
    monthly = [request.date_or_month for request in requests if request.kind == "monthly"]

    assert daily[0] == "2025-08-17"
    assert daily[14] == "2025-08-31"
    assert daily[15] == "2026-08-01"
    assert daily[-1] == "2026-08-16"
    assert len(daily) == 31
    assert monthly == [
        "2025-09",
        "2025-10",
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]


def test_archive_url_uses_usdm_kline_archive_path():
    request = archive_requests(date(2025, 8, 17), date(2026, 8, 17), "15m")[0]

    assert archive_url(request) == (
        "https://data.binance.vision/data/futures/um/daily/klines/"
        "BTCUSDT/15m/BTCUSDT-15m-2025-08-17.zip"
    )


def test_direct_opener_bypasses_inherited_proxy_configuration():
    opener = build_direct_opener()

    assert all(handler.__class__.__name__ != "ProxyHandler" for handler in opener.handlers)


def test_normalize_and_validate_filters_range_and_writes_utc_timestamp():
    start = utc_ms("2026-08-15T00:00:00")
    rows = [raw_kline(start - 900_000), raw_kline(start), raw_kline(start + 900_000)]

    normalized = normalize_and_validate(rows, "15m", start, start + 1_800_000)

    assert [row["open_time_ms"] for row in normalized] == [str(start), str(start + 900_000)]
    assert normalized[0]["open_time_utc"] == "2026-08-15T00:00:00Z"
    assert normalized[0]["close"] == "100.5"


def test_normalize_and_validate_rejects_missing_interval():
    start = utc_ms("2026-08-15T00:00:00")

    with pytest.raises(ValueError, match="missing"):
        normalize_and_validate(
            [raw_kline(start), raw_kline(start + 1_800_000)],
            "15m",
            start,
            start + 2_700_000,
        )
