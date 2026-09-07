from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from btc5m.clock import ReceiveStamp
from btc5m.collectors.live import (
    EventWriterPool,
    binance_stream_url,
    coinbase_subscribe_payloads,
    collection_status,
    hyperliquid_subscribe_payloads,
    missing_channels,
    stale_sources,
)
from btc5m.collectors.polymarket import MarketIdentity, active_token_ids
from btc5m.coverage import CoverageTracker
from btc5m.events import RawEvent


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


def test_missing_channels_reports_chainlink_one_sided_stop() -> None:
    assert missing_channels(
        ("chainlink",), {"chainlink/chainlink_twap_30": 3}
    ) == ["chainlink/chainlink_twap_60"]


def test_event_writer_flushes_after_a_slow_save_without_losing_rows(tmp_path) -> None:
    from btc5m.collectors import live

    event_counts: dict[str, int] = {}
    event_counts_by_type: dict[str, int] = {}
    event_paths: set = set()
    last_received: dict[str, float] = {}
    last_channels: dict[str, float] = {}
    tracker = CoverageTracker(max_gap_seconds=1)
    received = ReceiveStamp(datetime(2026, 9, 7, tzinfo=timezone.utc), time.monotonic_ns())
    event = RawEvent.from_message(
        source="binance",
        symbol="BTCUSDT",
        event_type="book_ticker",
        payload={"u": 1},
        received=received,
        source_event_ts=1_700_000_000_000,
        source_publish_ts=None,
        sequence_id="1",
        price=None,
        bid=Decimal("100"),
        ask=Decimal("101"),
        bid_size=None,
        ask_size=None,
    )
    original_append = live.append_jsonl_rows

    def delayed_append(path, rows):
        time.sleep(0.01)
        original_append(path, rows)

    live.append_jsonl_rows = delayed_append

    async def run() -> None:
        pool = EventWriterPool(
            output_root=tmp_path,
            sources=("binance",),
            event_counts=event_counts,
            event_counts_by_type=event_counts_by_type,
            event_paths=event_paths,
            last_received_monotonic=last_received,
            last_received_by_channel=last_channels,
            coverage_tracker=tracker,
            error_sink=None,
            queue_maxsize=1,
        )
        await pool.start()
        await pool.submit([event], connection_id="binance-1")
        await pool.close()

    try:
        asyncio.run(run())
    finally:
        live.append_jsonl_rows = original_append

    assert event_counts == {"binance": 1}
    assert event_counts_by_type == {"binance/book_ticker": 1}
    saved = next(tmp_path.rglob("events.jsonl"))
    assert '"connection_id": "binance-1"' in saved.read_text(encoding="utf-8")


def test_collect_public_reports_chainlink_one_sided_stop(
    monkeypatch,
    tmp_path,
) -> None:
    from btc5m.collectors import live

    received = ReceiveStamp(datetime.now(timezone.utc), time.monotonic_ns())
    event = RawEvent.from_message(
        source="chainlink",
        symbol="btc/usd",
        event_type="chainlink_twap_30",
        payload={"symbol": "btc/usd", "value": "100"},
        received=received,
        source_event_ts=1_700_000_000_000,
        source_publish_ts=None,
        sequence_id="30-1",
        price=Decimal("100"),
        bid=None,
        ask=None,
        bid_size=None,
        ask_size=None,
    )

    async def one_sided(*args, **kwargs) -> None:
        await kwargs["writers"].submit([event], connection_id="chainlink-1")
        await kwargs["stop"].wait()

    monkeypatch.setattr(live, "run_polymarket_source", one_sided)
    result = asyncio.run(
        live.collect_public(
            ("chainlink",),
            tmp_path,
            1,
            max_stale_seconds=0.1,
        )
    )

    assert result["status"] == "partial"
    assert result["missing_channels"] == ["chainlink/chainlink_twap_60"]


def test_market_discovery_failure_is_logged_and_does_not_stop_chainlink(
    monkeypatch,
    tmp_path,
) -> None:
    from btc5m.collectors import live

    errors: list[str] = []
    stop = asyncio.Event()

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        def list_markets(self, **kwargs):
            raise RuntimeError("catalog unavailable")

    monkeypatch.setitem(sys.modules, "polymarket", SimpleNamespace(AsyncPublicClient=Client))

    async def run() -> None:
        task = asyncio.create_task(
            live.run_polymarket_source(
                tmp_path,
                stop,
                include_market=True,
                include_chainlink=False,
                market_refresh_seconds=0.1,
                error_sink=errors.append,
            )
        )
        await asyncio.sleep(0.02)
        stop.set()
        await task

    asyncio.run(run())

    assert any("polymarket_market_discovery" in error for error in errors)
    connection_log = tmp_path / "logs" / "connections.jsonl"
    assert "polymarket_market_discovery" in connection_log.read_text(encoding="utf-8")


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
    sdk_drops: dict[str, int] = {}

    class EmptyStream:
        dropped = 2

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
            sdk_drop_counts=sdk_drops,
        )
    )

    assert subscriptions == [("u1", "d1"), ("u1", "d1", "u2", "d2")]
    assert sdk_drops == {"polymarket": 2}


def test_chainlink_subscription_is_not_restarted_by_market_refresh(
    monkeypatch,
    tmp_path,
) -> None:
    from btc5m.collectors import live

    now = datetime.now(timezone.utc)
    now_us = int(now.timestamp() * 1_000_000)
    markets = [MarketIdentity("current", "c1", "u1", "d1", now_us, now_us + 300_000_000)]
    stop = asyncio.Event()
    market_subscriptions = 0
    chainlink_subscriptions = 0

    class EmptyMarketStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class WaitingChainlinkStream:
        dropped = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            await stop.wait()
            raise StopAsyncIteration

    class Subscription:
        def __init__(self, stream):
            self.stream = stream

        async def __aenter__(self):
            return self.stream

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def subscribe(self, specs):
            nonlocal market_subscriptions, chainlink_subscriptions
            if hasattr(specs[0], "token_ids"):
                market_subscriptions += 1
                if market_subscriptions >= 2 and chainlink_subscriptions >= 1:
                    stop.set()
                return Subscription(EmptyMarketStream())
            chainlink_subscriptions += 1
            return Subscription(WaitingChainlinkStream())

    async def discover(client):
        await asyncio.sleep(0)
        return markets

    monkeypatch.setitem(
        sys.modules, "polymarket", SimpleNamespace(AsyncPublicClient=Client)
    )
    monkeypatch.setattr(live, "discover_btc_5m_markets", discover)
    monkeypatch.setattr(
        live,
        "build_market_specs",
        lambda token_ids: (SimpleNamespace(token_ids=tuple(token_ids)),),
    )
    monkeypatch.setattr(
        live,
        "build_chainlink_specs",
        lambda: (SimpleNamespace(window_seconds=30), SimpleNamespace(window_seconds=60)),
    )

    async def invoke() -> None:
        await asyncio.wait_for(
            live.run_polymarket_source(
                tmp_path,
                stop,
                include_market=True,
                include_chainlink=True,
                market_refresh_seconds=0.1,
            ),
            timeout=2.0,
        )

    asyncio.run(invoke())

    assert market_subscriptions == 2
    assert chainlink_subscriptions == 1


def test_polymarket_cancellation_records_final_sdk_drop_count(
    monkeypatch,
    tmp_path,
) -> None:
    from btc5m.collectors import live

    stop = asyncio.Event()
    sdk_drops: dict[str, int] = {}

    class Stream:
        dropped = 7

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Future()
            raise StopAsyncIteration

    class Subscription:
        async def __aenter__(self):
            return Stream()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def subscribe(self, specs):
            return Subscription()

    monkeypatch.setitem(
        sys.modules, "polymarket", SimpleNamespace(AsyncPublicClient=Client)
    )
    monkeypatch.setattr(
        live,
        "build_chainlink_specs",
        lambda: (SimpleNamespace(window_seconds=30), SimpleNamespace(window_seconds=60)),
    )

    async def run() -> None:
        task = asyncio.create_task(
            live.run_polymarket_source(
                tmp_path,
                stop,
                include_market=False,
                include_chainlink=True,
                sdk_drop_counts=sdk_drops,
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())

    assert sdk_drops == {"chainlink": 7}
