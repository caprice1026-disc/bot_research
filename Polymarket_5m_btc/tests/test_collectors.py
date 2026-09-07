from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from btc5m.clock import ReceiveStamp
from btc5m.collectors.binance import parse_binance_message
from btc5m.collectors.chainlink import build_chainlink_specs, parse_chainlink_message
from btc5m.collectors.coinbase import parse_coinbase_message
from btc5m.collectors.common import reconnect_forever
from btc5m.collectors.hyperliquid import parse_hyperliquid_message
from btc5m.collectors.polymarket import (
    build_market_specs,
    discover_btc_5m_markets,
    market_identity_from_mapping,
    parse_polymarket_message,
)

RECEIVED = ReceiveStamp(datetime(2026, 9, 7, tzinfo=timezone.utc), 10)


def test_binance_aggtrade_parser_preserves_trade_fields() -> None:
    events = parse_binance_message(
        {
            "e": "aggTrade",
            "E": 1_700_000_000_123,
            "s": "BTCUSDT",
            "a": 42,
            "p": "100.25",
            "q": "0.01",
            "T": 1_700_000_000_120,
            "m": False,
        },
        RECEIVED,
    )

    assert len(events) == 1
    assert events[0].event_type == "agg_trade"
    assert events[0].price == Decimal("100.25")
    assert events[0].source_event_ts == 1_700_000_000_120_000
    assert events[0].sequence_id == "42"


def test_coinbase_ticker_parser_reads_nested_update() -> None:
    events = parse_coinbase_message(
        {
            "channel": "ticker",
            "timestamp": "2026-09-07T00:00:00.100Z",
            "sequence_num": 7,
            "events": [
                {
                    "type": "update",
                    "tickers": [
                        {
                            "product_id": "BTC-USD",
                            "price": "100.25",
                            "best_bid": "100.20",
                            "best_ask": "100.30",
                            "time": "2026-09-07T00:00:00.090Z",
                        }
                    ],
                }
            ],
        },
        RECEIVED,
    )

    assert len(events) == 1
    assert events[0].event_type == "ticker"
    assert events[0].bid == Decimal("100.20")
    assert events[0].ask == Decimal("100.30")
    assert events[0].source_publish_ts == 1_788_739_200_100_000


def test_hyperliquid_bbo_parser_reads_bid_ask() -> None:
    events = parse_hyperliquid_message(
        {
            "channel": "bbo",
            "data": {
                "coin": "BTC",
                "time": 1_700_000_000_123,
                "bbo": [
                    {"px": "100.20", "sz": "1.0"},
                    {"px": "100.30", "sz": "2.0"},
                ],
            },
        },
        RECEIVED,
    )

    assert len(events) == 1
    assert events[0].event_type == "bbo"
    assert events[0].bid == Decimal("100.20")
    assert events[0].ask_size == Decimal("2.0")


def test_polymarket_book_parser_reads_best_levels() -> None:
    events = parse_polymarket_message(
        {
            "event_type": "book",
            "asset_id": "up-token",
            "market": "condition",
            "timestamp": "1700000000123",
            "bids": [{"price": "0.48", "size": "30"}],
            "asks": [{"price": "0.52", "size": "25"}],
        },
        RECEIVED,
    )

    assert len(events) == 1
    assert events[0].event_type == "book"
    assert events[0].bid == Decimal("0.48")
    assert events[0].ask == Decimal("0.52")


def test_polymarket_parser_unwraps_official_sdk_payload() -> None:
    events = parse_polymarket_message(
        {
            "topic": "market",
            "type": "best_bid_ask",
            "payload": {
                "asset_id": "up-token",
                "market": "condition",
                "best_bid": "0.60",
                "best_ask": "0.62",
                "timestamp": "1700000000123",
            },
        },
        RECEIVED,
    )

    assert len(events) == 1
    assert events[0].event_type == "best_bid_ask"
    assert events[0].bid == Decimal("0.60")


def test_chainlink_parser_separates_observed_and_publish_times() -> None:
    events = parse_chainlink_message(
        {
            "topic": "prices.crypto.chainlink.twap",
            "timestamp": 1_700_000_000_500,
            "payload": {
                "symbol": "btc/usd",
                "timestamp": 1_700_000_000_400,
                "value": "100.25",
                "window_seconds": 60,
            },
        },
        RECEIVED,
        window_seconds=60,
    )

    assert len(events) == 1
    assert events[0].event_type == "chainlink_twap_60"
    assert events[0].price == Decimal("100.25")
    assert events[0].source_event_ts == 1_700_000_000_400_000
    assert events[0].source_publish_ts == 1_700_000_000_500_000


def test_reconnect_runner_retries_until_stop() -> None:
    calls = 0
    received: list[tuple[str, int]] = []
    stop = asyncio.Event()

    async def first_stream():
        yield {"id": 1}
        raise ConnectionError("simulated disconnect")

    async def second_stream():
        yield {"id": 2}

    def connect():
        nonlocal calls
        calls += 1
        return first_stream() if calls == 1 else second_stream()

    async def handle(message, stamp):
        received.append((str(message["id"]), stamp.local_monotonic_ns))
        if message["id"] == 2:
            stop.set()

    asyncio.run(
        reconnect_forever(
            "fixture",
            connect,
            handle,
            stop,
            reconnect_base_seconds=0,
            reconnect_max_seconds=0,
        )
    )

    assert calls == 2
    assert [item[0] for item in received] == ["1", "2"]


def test_market_identity_extracts_five_minute_window_and_tokens() -> None:
    identity = market_identity_from_mapping(
        {
            "slug": "btc-updown-5m-1700000000",
            "question": "BTC Up or Down 5m",
            "condition_id": "condition",
            "outcomes": {
                "yes": {"token_id": "up-token"},
                "no": {"token_id": "down-token"},
            },
        }
    )

    assert identity is not None
    assert identity.up_token_id == "up-token"
    assert identity.window_end_ts - identity.window_start_ts == 300_000_000


def test_market_discovery_accepts_official_sync_paginator() -> None:
    class Page:
        items = [
            {
                "slug": "btc-updown-5m-1700000000",
                "question": "BTC Up or Down 5m",
                "condition_id": "condition",
                "outcomes": {"yes": "up-token", "no": "down-token"},
            }
        ]

    class Client:
        def list_markets(self, **kwargs):
            assert kwargs == {
                "closed": False,
                "end_date_min": "2026-09-06T23:55:00+00:00",
                "end_date_max": "2026-09-07T00:10:00+00:00",
                "order": "endDate",
                "ascending": True,
                "page_size": 100,
            }

            async def pages():
                yield Page()

            return pages()

    identities = asyncio.run(
        discover_btc_5m_markets(Client(), now=datetime(2026, 9, 7, tzinfo=timezone.utc))
    )

    assert [identity.slug for identity in identities] == ["btc-updown-5m-1700000000"]


def test_subscription_builders_include_market_and_chainlink_windows() -> None:
    market_specs = build_market_specs(["up-token", "down-token"])
    chainlink_specs = build_chainlink_specs()

    assert market_specs[0].token_ids == ("up-token", "down-token")
    assert market_specs[0].custom_feature_enabled is True
    assert [spec.window_seconds for spec in chainlink_specs] == [30, 60]


def test_polymarket_price_changes_use_composite_sequence_ids() -> None:
    events = parse_polymarket_message(
        {
            "event_type": "price_change",
            "timestamp": 1_700_000_000_000,
            "price_changes": [
                {"asset_id": "up", "hash": "same", "price": "0.50"},
                {"asset_id": "up", "hash": "same", "price": "0.51"},
            ],
        },
        RECEIVED,
    )

    assert len({event.sequence_id for event in events}) == 2
