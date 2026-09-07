from __future__ import annotations

import pytest

from btc5m.events import parse_source_timestamp
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
        _row(
            "binance",
            "2026-09-07T00:00:00.000000Z",
            "100",
            symbol="BTCUSDT",
            event_type="agg_trade",
        ),
        _row(
            "binance",
            "2026-09-07T00:00:00.500000Z",
            "101",
            symbol="BTCUSDT",
            event_type="agg_trade",
        ),
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
    assert result.observations_by_horizon == {100: 1, 200: 1}
    assert result.mean_signed_response_by_horizon[100] == pytest.approx(0.0)
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
        _row(
            "polymarket",
            "2026-09-07T00:00:00.000000Z",
            "0.70",
            symbol="up",
            market_id="m1",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.000000Z",
            "0.30",
            symbol="down",
            market_id="m1",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.700000Z",
            "0.80",
            symbol="up",
            market_id="m1",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.700000Z",
            "0.20",
            symbol="down",
            market_id="m1",
        ),
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        horizons_ms=(200,),
    )

    assert result.observations_by_horizon == {200: 0}
    assert result.mean_signed_response_by_horizon[200] is None
    assert result.series_results["m1/up/price"].mean_signed_response_by_horizon[
        200
    ] == pytest.approx(0.10)
    assert result.series_results["m1/down/price"].mean_signed_response_by_horizon[
        200
    ] == pytest.approx(-0.10)


def test_event_study_does_not_combine_midpoint_and_last_trade_price() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row(
            "polymarket",
            "2026-09-07T00:00:00.000000Z",
            "0.50",
            symbol="up",
            market_id="m1",
            event_type="best_bid_ask",
            bid="0.49",
            ask="0.51",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.700000Z",
            "0.50",
            symbol="up",
            market_id="m1",
            event_type="best_bid_ask",
            bid="0.49",
            ask="0.51",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.700000Z",
            "0.40",
            symbol="up",
            market_id="m1",
            event_type="last_trade_price",
        ),
    ]

    result = event_study(external, polymarket, shock_return=0.005, horizons_ms=(200,))

    assert result.observations_by_horizon == {200: 0}
    assert result.series_results["m1/up/quote_mid"].mean_signed_response_by_horizon[
        200
    ] == pytest.approx(0.0)
    assert (
        result.series_results["m1/up/trade"].mean_signed_response_by_horizon[200]
        is None
    )


def test_event_study_ignores_changed_price_level_when_best_quotes_are_unchanged() -> (
    None
):
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row(
            "polymarket",
            "2026-09-07T00:00:00.000000Z",
            "0.60",
            symbol="up",
            market_id="m1",
            event_type="best_bid_ask",
            bid="0.59",
            ask="0.61",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.700000Z",
            "0.01",
            symbol="up",
            market_id="m1",
            event_type="price_change",
            bid="0.59",
            ask="0.61",
        ),
    ]

    result = event_study(external, polymarket, shock_return=0.005, horizons_ms=(200,))

    assert result.mean_signed_response_by_horizon[200] == pytest.approx(0.0)


def test_event_study_does_not_use_tail_for_uncovered_horizon() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row(
            "polymarket",
            "2026-09-07T00:00:00.000000Z",
            "0.50",
            symbol="up",
            market_id="m1",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.700000Z",
            "0.60",
            symbol="up",
            market_id="m1",
        ),
    ]

    result = event_study(external, polymarket, shock_return=0.005, horizons_ms=(5_000,))

    assert result.status == "insufficient_data"
    assert result.observations_by_horizon == {5_000: 0}
    assert result.mean_signed_response_by_horizon[5_000] is None


def test_event_study_rejects_stale_lookback_and_deduplicates_same_shock() -> None:
    external = [
        _row(
            "binance",
            "2026-09-07T00:00:00.000000Z",
            "100",
            symbol="BTCUSDT",
            event_type="agg_trade",
        ),
        _row(
            "binance",
            "2026-09-07T00:00:00.500000Z",
            "101",
            symbol="BTCUSDT",
            event_type="agg_trade",
        ),
        _row(
            "binance",
            "2026-09-07T00:00:00.510000Z",
            "101.1",
            symbol="BTCUSDT",
            event_type="agg_trade",
        ),
        _row("coinbase", "2026-09-07T00:00:00.511000Z", "101.2"),
        _row("binance", "2026-09-07T00:00:03.000000Z", "102"),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.50"),
        _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.60"),
        _row("polymarket", "2026-09-07T00:00:03.200000Z", "0.70"),
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        lookback_ms=500,
        max_shock_age_ms=100,
        shock_cooldown_ms=100,
        horizons_ms=(200,),
    )

    assert result.event_count == 1
    assert result.shock_provenance == {"binance/trade": 1}


def test_event_study_does_not_cross_a_reported_gap() -> None:
    external = [
        _row(
            "binance",
            "2026-09-07T00:00:00.000000Z",
            "100",
            symbol="BTCUSDT",
            event_type="agg_trade",
        ),
        _row(
            "binance",
            "2026-09-07T00:00:00.500000Z",
            "101",
            symbol="BTCUSDT",
            event_type="agg_trade",
        ),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.50"),
        _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.60"),
    ]
    gaps = [
        {
            "source": "binance",
            "channel": "trade",
            "symbol": "BTCUSDT",
            "start_ts": parse_source_timestamp(
                "2026-09-07T00:00:00.250000Z"
            ),
            "end_ts": parse_source_timestamp("2026-09-07T00:00:00.450000Z"),
        }
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        horizons_ms=(200,),
        gap_intervals=gaps,
    )

    assert result.event_count == 0
    assert result.reason == "no_external_shocks"
