from __future__ import annotations

import pytest

from btc5m.research.lead_lag import event_study


def _row(
    source: str,
    received: str,
    price: str,
    *,
    symbol: str | None = None,
    market_id: str | None = None,
    event_type: str = "fixture_price",
    bid: str | None = None,
    ask: str | None = None,
) -> dict[str, object]:
    return {
        "source": source,
        "local_receive_ts": received,
        "price": price,
        "bid": bid,
        "ask": ask,
        "symbol": symbol,
        "market_id": market_id,
        "event_type": event_type,
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


def test_event_study_does_not_mix_up_and_down_token_series() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.70", symbol="up", market_id="m1"),
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.30", symbol="down", market_id="m1"),
        _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.70", symbol="up", market_id="m1"),
        _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.30", symbol="down", market_id="m1"),
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        horizons_ms=(200,),
    )

    assert result.observations_by_horizon == {200: 2}
    assert result.mean_signed_response_by_horizon[200] == pytest.approx(0.0)


def test_event_study_ignores_changed_price_level_when_best_quotes_are_unchanged() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.60", symbol="up", market_id="m1", event_type="best_bid_ask", bid="0.59", ask="0.61"),
        _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.01", symbol="up", market_id="m1", event_type="price_change", bid="0.59", ask="0.61"),
    ]

    result = event_study(external, polymarket, shock_return=0.005, horizons_ms=(200,))

    assert result.mean_signed_response_by_horizon[200] == pytest.approx(0.0)


def test_event_study_does_not_use_tail_for_uncovered_horizon() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.50", symbol="up", market_id="m1"),
        _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.60", symbol="up", market_id="m1"),
    ]

    result = event_study(external, polymarket, shock_return=0.005, horizons_ms=(5_000,))

    assert result.status == "insufficient_data"
    assert result.observations_by_horizon == {5_000: 0}
    assert result.mean_signed_response_by_horizon[5_000] is None
