from __future__ import annotations

import pytest

from btc5m.research.lead_lag import event_study


def _row(source: str, received: str, price: str) -> dict[str, object]:
    return {
        "source": source,
        "local_receive_ts": received,
        "price": price,
        "bid": None,
        "ask": None,
    }


def test_event_study_uses_receive_time_and_signed_response() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.50"),
        _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.60"),
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        horizons_ms=(100, 200),
    )

    assert result.status == "exploratory"
    assert result.event_count == 1
    assert result.observations_by_horizon == {100: 0, 200: 1}
    assert result.mean_signed_response_by_horizon[200] == pytest.approx(0.10)


def test_event_study_reports_insufficient_data_without_receive_timestamps() -> None:
    rows = [{"source": "binance", "source_event_ts": 1, "price": "100"}]
    result = event_study(rows, rows, shock_return=0.001, horizons_ms=(100,))

    assert result.status == "insufficient_data"
    assert result.event_count == 0
    assert result.reason == "missing_receive_time"
