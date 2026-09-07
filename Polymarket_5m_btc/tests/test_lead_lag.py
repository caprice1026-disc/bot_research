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


def test_event_study_applies_decision_intervals_per_market() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.50", symbol="up", market_id="m1"),
        _row("polymarket", "2026-09-07T00:00:01.000000Z", "0.60", symbol="up", market_id="m1"),
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.50", symbol="up", market_id="m2"),
        _row("polymarket", "2026-09-07T00:00:01.000000Z", "0.60", symbol="up", market_id="m2"),
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        horizons_ms=(200,),
        decision_intervals={
            "m1": [(1_788_739_201_000_000, 1_788_739_202_000_000)],
            "m2": [(1_788_739_200_000_000, 1_788_739_201_000_000)],
        },
    )

    assert result.series_results["m1/up/price"].observations_by_horizon == {200: 0}
    assert result.series_results["m2/up/price"].observations_by_horizon == {200: 1}


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


def test_event_study_deduplicates_a_persistent_threshold_crossing() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100", event_type="agg_trade"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101", event_type="agg_trade"),
        _row("binance", "2026-09-07T00:00:00.600000Z", "101", event_type="agg_trade"),
        _row("binance", "2026-09-07T00:00:00.700000Z", "101", event_type="agg_trade"),
        _row("binance", "2026-09-07T00:00:00.800000Z", "101", event_type="agg_trade"),
    ]
    polymarket = [
        _row("polymarket", "2026-09-07T00:00:00.000000Z", "0.50"),
        _row("polymarket", "2026-09-07T00:00:01.000000Z", "0.60"),
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        lookback_ms=500,
        shock_cooldown_ms=100,
        horizons_ms=(100,),
    )

    assert result.event_count == 1


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


def test_event_study_does_not_cross_polymarket_trade_gap() -> None:
    external = [
        _row("binance", "2026-09-07T00:00:00.000000Z", "100"),
        _row("binance", "2026-09-07T00:00:00.500000Z", "101"),
    ]
    polymarket = [
        _row(
            "polymarket",
            "2026-09-07T00:00:00.000000Z",
            "0.50",
            market_id="m1",
            symbol="up",
            event_type="last_trade_price",
        ),
        _row(
            "polymarket",
            "2026-09-07T00:00:00.700000Z",
            "0.60",
            market_id="m1",
            symbol="up",
            event_type="last_trade_price",
        ),
    ]
    gaps = [
        {
            "source": "polymarket",
            "channel": "last_trade_price",
            "symbol": "up",
            "market_id": "m1",
            "start_ts": parse_source_timestamp("2026-09-07T00:00:00.200000Z"),
            "end_ts": parse_source_timestamp("2026-09-07T00:00:00.600000Z"),
        }
    ]

    result = event_study(
        external,
        polymarket,
        shock_return=0.005,
        horizons_ms=(200,),
        gap_intervals=gaps,
    )

    assert result.series_results["m1/up/trade"].gap_excluded_by_horizon == {200: 1}


def test_response_rejects_pre_shock_gap_before_stale_baseline() -> None:
    result = event_study(
        [_row("binance", "2026-09-07T00:00:00Z", "100"),
         _row("binance", "2026-09-07T00:00:00.500000Z", "101")],
        [_row("polymarket", "2026-09-07T00:00:00Z", "0.5"),
         _row("polymarket", "2026-09-07T00:00:00.700000Z", "0.6")],
        horizons_ms=(200,),
        gap_intervals=[{
            "source": "polymarket", "channel": "price",
            "start_ts": 1_788_739_200_100_000,
            "end_ts": 1_788_739_200_400_000,
        }],
    )
    assert result.observations_by_horizon == {200: 0}
