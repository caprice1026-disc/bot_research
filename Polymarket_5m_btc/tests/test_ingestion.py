from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from btc5m.ingestion.binance_history import binance_daily_url, normalize_aggtrade_rows
from btc5m.ingestion.pmxt import (
    CoverageManifest,
    download_pmxt_hours,
    inspect_pmxt_coverage,
    pmxt_url,
    requested_utc_hours,
)


def test_requested_hours_and_pmxt_index_coverage() -> None:
    start = datetime(2026, 8, 10, 0, 30, tzinfo=timezone.utc)
    end = datetime(2026, 8, 10, 3, 0, tzinfo=timezone.utc)
    html = """
    <a href="polymarket_orderbook_2026-08-10T00.parquet">00</a>
    <a href="polymarket_orderbook_2026-08-10T02.parquet">02</a>
    """

    assert requested_utc_hours(start, end) == [
        datetime(2026, 8, 10, hour, tzinfo=timezone.utc) for hour in range(3)
    ]
    manifest = inspect_pmxt_coverage(start, end, html)
    assert manifest.present_hours == (
        datetime(2026, 8, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 10, 2, tzinfo=timezone.utc),
    )
    assert manifest.missing_hours == (datetime(2026, 8, 10, 1, tzinfo=timezone.utc),)
    assert pmxt_url(manifest.present_hours[0]).endswith("2026-08-10T00.parquet")


def test_pmxt_download_skips_existing_validated_file(tmp_path) -> None:
    hour = datetime(2026, 8, 10, tzinfo=timezone.utc)
    manifest = CoverageManifest(
        requested_hours=(hour,),
        present_hours=(hour,),
        missing_hours=(),
        urls=(pmxt_url(hour),),
    )
    output = tmp_path / "archive"
    output.mkdir()
    existing = output / "polymarket_orderbook_2026-08-10T00.parquet"
    existing.write_bytes(b"already-there")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("validated existing file must not be downloaded")

    result = download_pmxt_hours(
        manifest,
        output,
        httpx.Client(transport=httpx.MockTransport(handler)),
        allow_download=True,
    )

    assert result.skipped == (existing.name,)
    assert result.downloaded == ()


def test_pmxt_download_marks_http_404_as_missing(tmp_path) -> None:
    hour = datetime(2026, 8, 10, tzinfo=timezone.utc)
    manifest = CoverageManifest(
        requested_hours=(hour,),
        present_hours=(hour,),
        missing_hours=(),
        urls=(pmxt_url(hour),),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    result = download_pmxt_hours(
        manifest,
        tmp_path,
        httpx.Client(transport=httpx.MockTransport(handler)),
        allow_download=True,
    )

    assert result.missing == ("polymarket_orderbook_2026-08-10T00.parquet",)
    assert result.errors == ()


def test_binance_daily_url_and_historical_event_have_no_receive_time() -> None:
    assert binance_daily_url("BTCUSDT", date(2026, 9, 7)).endswith(
        "/BTCUSDT-aggTrades-2026-09-07.zip"
    )

    event = normalize_aggtrade_rows(
        [["42", "100.25", "0.01", "42", "42", "1700000000123", "false"]]
    )[0]

    assert event.price == 100.25
    assert event.source_event_ts == 1_700_000_000_123_000
    assert event.local_receive_ts is None
    assert event.local_monotonic_ns is None
