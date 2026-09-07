from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from btc5m.collectors.live import (
    binance_stream_url,
    coinbase_subscribe_payloads,
    collection_status,
    hyperliquid_subscribe_payloads,
    stale_sources,
)
from btc5m.collectors.polymarket import MarketIdentity, active_token_ids


def test_binance_combined_stream_contains_trade_and_book_ticker() -> None:
    url = binance_stream_url("BTCUSDT")

    assert url == (
        "wss://stream.binance.com:9443/stream?"
        "streams=btcusdt@aggTrade/btcusdt@bookTicker"
    )


def test_coinbase_subscriptions_are_public_btc_messages() -> None:
    messages = coinbase_subscribe_payloads(["BTC-USD"])

    assert messages == [
        {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "ticker"},
        {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "market_trades"},
    ]


def test_hyperliquid_subscriptions_cover_trades_and_bbo() -> None:
    messages = hyperliquid_subscribe_payloads("BTC")

    assert messages == [
        {"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}},
        {"method": "subscribe", "subscription": {"type": "bbo", "coin": "BTC"}},
    ]


def test_market_refresh_keeps_current_and_next_five_minute_tokens() -> None:
    now = datetime.now(timezone.utc)
    now_us = int(now.timestamp() * 1_000_000)

    assert active_token_ids(
        [
            MarketIdentity("current", "c1", "u1", "d1", now_us - 1, now_us + 1),
            MarketIdentity("next", "c2", "u2", "d2", now_us + 1, now_us + 301_000_000),
        ],
        now=now,
        grace_seconds=60,
    ) == ["u1", "d1", "u2", "d2"]


def test_collection_status_does_not_call_empty_or_partial_runs_ok() -> None:
    assert collection_status(("binance",), {}, ()) == "no_events"
    assert (
        collection_status(("binance",), {"binance": 1}, ("network error",)) == "partial"
    )
    assert collection_status(("binance", "coinbase"), {"binance": 1}, ()) == "partial"
    assert collection_status(("binance",), {"binance": 1}, ()) == "ok"


def test_collection_status_marks_a_source_that_stopped_after_one_event_partial() -> (
    None
):
    stale = stale_sources(
        ("binance",),
        {"binance": 10.0},
        now_monotonic=101.0,
        max_stale_seconds=90.0,
    )

    assert stale == ["binance"]
    assert collection_status(("binance",), {"binance": 1}, (), stale) == "partial"


def test_collect_public_marks_a_collector_that_stops_without_events_error(
    monkeypatch,
    tmp_path,
) -> None:
    from btc5m.collectors import live

    old_file = tmp_path / "binance" / "date=2026-09-06" / "hour=23" / "events.jsonl"
    old_file.parent.mkdir(parents=True)
    old_file.write_text('{"source":"binance"}\n', encoding="utf-8")

    async def no_events(**kwargs) -> None:
        return None

    monkeypatch.setattr(live, "run_external_source", no_events)
    result = asyncio.run(live.collect_public(("binance",), tmp_path, 1))

    assert result["status"] == "error"
    assert result["event_counts"] == {}
    assert result["event_files"] == []
    assert result["errors"] == ["binance: collector stopped without stop signal"]


def test_collect_public_marks_an_idle_source_partial(
    monkeypatch,
    tmp_path,
) -> None:
    from btc5m.collectors import live

    async def one_old_event(**kwargs) -> None:
        kwargs["event_counts"]["binance"] = 1
        kwargs["last_received_monotonic"]["binance"] = time.monotonic() - 1.0
        await kwargs["stop"].wait()

    monkeypatch.setattr(live, "run_external_source", one_old_event)
    result = asyncio.run(
        live.collect_public(
            ("binance",),
            tmp_path,
            1,
            max_stale_seconds=0.1,
        )
    )

    assert result["status"] == "partial"
    assert result["stale_sources"] == ["binance"]


def test_polymarket_source_refreshes_subscription_for_next_market(
    monkeypatch,
    tmp_path,
) -> None:
    from btc5m.collectors import live

    now = datetime.now(timezone.utc)
    now_us = int(now.timestamp() * 1_000_000)
    markets = [
        MarketIdentity("current", "c1", "u1", "d1", now_us, now_us + 300_000_000),
        MarketIdentity(
            "next", "c2", "u2", "d2", now_us + 300_000_000, now_us + 600_000_000
        ),
    ]
    stop = asyncio.Event()
    subscriptions: list[tuple[str, ...]] = []
    discovery_calls = 0

    class EmptyStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class Subscription:
        async def __aenter__(self):
            return EmptyStream()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def subscribe(self, specs):
            subscriptions.append(tuple(specs[0].token_ids))
            if len(subscriptions) == 2:
                stop.set()
            return Subscription()

    async def discover(client):
        nonlocal discovery_calls
        discovery_calls += 1
        return markets[:1] if discovery_calls == 1 else markets

    monkeypatch.setitem(
        sys.modules, "polymarket", SimpleNamespace(AsyncPublicClient=Client)
    )
    monkeypatch.setattr(live, "discover_btc_5m_markets", discover)
    monkeypatch.setattr(
        live,
        "build_market_specs",
        lambda token_ids: (SimpleNamespace(token_ids=tuple(token_ids)),),
    )

    asyncio.run(
        live.run_polymarket_source(
            tmp_path,
            stop,
            include_market=True,
            include_chainlink=False,
            market_refresh_seconds=0.1,
        )
    )

    assert subscriptions == [("u1", "d1"), ("u1", "d1", "u2", "d2")]
