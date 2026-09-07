from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from btc5m.clock import ReceiveStamp
from btc5m.collectors.binance import parse_binance_message
from btc5m.collectors.chainlink import build_chainlink_specs, parse_chainlink_message
from btc5m.collectors.coinbase import parse_coinbase_message
from btc5m.collectors.common import reconnect_forever
from btc5m.collectors.hyperliquid import parse_hyperliquid_message
from btc5m.collectors.polymarket import (
    build_market_specs,
    discover_btc_5m_markets,
    parse_polymarket_message,
)
from btc5m.events import RawEvent
from btc5m.io import write_json
from btc5m.market_master import write_market_master
from btc5m.storage import append_jsonl, partition_path

BINANCE_URI = "wss://stream.binance.com:9443/stream"
COINBASE_URI = "wss://advanced-trade-ws.coinbase.com"
HYPERLIQUID_URI = "wss://api.hyperliquid.xyz/ws"


def binance_stream_url(symbol: str) -> str:
    normalized = symbol.lower()
    return f"{BINANCE_URI}?streams={normalized}@aggTrade/{normalized}@bookTicker"


def coinbase_subscribe_payloads(products: Iterable[str]) -> list[dict[str, object]]:
    product_ids = list(products)
    return [
        {"type": "subscribe", "product_ids": product_ids, "channel": "ticker"},
        {"type": "subscribe", "product_ids": product_ids, "channel": "market_trades"},
    ]


def hyperliquid_subscribe_payloads(coin: str) -> list[dict[str, object]]:
    return [
        {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}},
        {"method": "subscribe", "subscription": {"type": "bbo", "coin": coin}},
    ]


async def websocket_messages(
    uri: str,
    subscribe_messages: Iterable[Mapping[str, object]] = (),
) -> AsyncIterator[Mapping[str, Any]]:
    import websockets

    async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as socket:
        for message in subscribe_messages:
            await socket.send(json.dumps(message, separators=(",", ":")))
        async for raw in socket:
            if raw in ("", "pong", b"pong"):
                continue
            decoded = json.loads(raw)
            if isinstance(decoded, Mapping):
                yield decoded


async def _write_events(output_root: Path, events: Iterable[RawEvent]) -> None:
    for event in events:
        if event.local_receive_ts is None:
            raise ValueError("live collector event is missing local_receive_ts")
        append_jsonl(
            partition_path(output_root, event.source, event.local_receive_ts),
            event.to_row(),
        )


async def run_external_source(
    *,
    source: str,
    uri: str,
    parser: Callable[[Mapping[str, Any], ReceiveStamp], list[RawEvent]],
    output_root: Path,
    stop: asyncio.Event,
    subscribe_messages: Iterable[Mapping[str, object]] = (),
) -> None:
    async def connect() -> AsyncIterator[Mapping[str, Any]]:
        async for message in websocket_messages(uri, subscribe_messages):
            yield message

    async def handle(message: Mapping[str, Any], received: ReceiveStamp) -> None:
        await _write_events(output_root, parser(message, received))

    await reconnect_forever(source, connect, handle, stop)


def _model_mapping(event: object) -> Mapping[str, Any]:
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", by_alias=True)
        if isinstance(dumped, Mapping):
            return dumped
        raise TypeError("SDK event model_dump did not return a mapping")
    if isinstance(event, Mapping):
        return event
    raise TypeError(f"unsupported SDK event: {type(event).__name__}")


async def run_polymarket_source(
    output_root: Path,
    stop: asyncio.Event,
    *,
    include_market: bool = True,
    include_chainlink: bool = True,
) -> None:
    from polymarket import AsyncPublicClient

    async with AsyncPublicClient() as client:
        token_ids: list[str] = []
        if include_market:
            markets = await discover_btc_5m_markets(client)
            write_market_master(
                output_root / "market_master" / "markets.parquet", markets
            )
            token_ids = [
                token_id
                for market in markets
                for token_id in (market.up_token_id, market.down_token_id)
            ]
        specs: list[Any] = []
        if include_market and token_ids:
            specs = list(build_market_specs(token_ids)) + specs
        if include_chainlink:
            specs.extend(build_chainlink_specs())
        if not specs:
            return
        async with await client.subscribe(specs) as stream:
            async for event in stream:
                if stop.is_set():
                    return
                received = ReceiveStamp(
                    datetime.now(timezone.utc),
                    time.monotonic_ns(),
                )
                mapping = _model_mapping(event)
                if mapping.get("topic") == "prices.crypto.chainlink.twap":
                    body = mapping.get("payload")
                    window = (
                        int(body.get("window_seconds", 60))
                        if isinstance(body, Mapping)
                        else 60
                    )
                    events = parse_chainlink_message(mapping, received, window)
                else:
                    events = parse_polymarket_message(mapping, received)
                await _write_events(output_root, events)


async def collect_public(
    sources: tuple[str, ...],
    output_root: Path,
    duration_seconds: int,
) -> dict[str, object]:
    if duration_seconds < 1:
        raise ValueError("duration_seconds must be at least 1")
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    errors: list[str] = []

    async def guarded(name: str, operation: Awaitable[None]) -> None:
        try:
            await operation
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    if "binance" in sources:
        tasks.append(
            asyncio.create_task(
                guarded(
                    "binance",
                    run_external_source(
                        source="binance",
                        uri=binance_stream_url("BTCUSDT"),
                        parser=parse_binance_message,
                        output_root=output_root,
                        stop=stop,
                    ),
                )
            )
        )
    if "coinbase" in sources:
        tasks.append(
            asyncio.create_task(
                guarded(
                    "coinbase",
                    run_external_source(
                        source="coinbase",
                        uri=COINBASE_URI,
                        parser=parse_coinbase_message,
                        output_root=output_root,
                        stop=stop,
                        subscribe_messages=coinbase_subscribe_payloads(["BTC-USD"]),
                    ),
                )
            )
        )
    if "hyperliquid" in sources:
        tasks.append(
            asyncio.create_task(
                guarded(
                    "hyperliquid",
                    run_external_source(
                        source="hyperliquid",
                        uri=HYPERLIQUID_URI,
                        parser=parse_hyperliquid_message,
                        output_root=output_root,
                        stop=stop,
                        subscribe_messages=hyperliquid_subscribe_payloads("BTC"),
                    ),
                )
            )
        )
    if "polymarket" in sources or "chainlink" in sources:
        tasks.append(
            asyncio.create_task(
                guarded(
                    "polymarket/chainlink",
                    run_polymarket_source(
                        output_root,
                        stop,
                        include_market="polymarket" in sources,
                        include_chainlink="chainlink" in sources,
                    ),
                )
            )
        )
    if not tasks:
        raise ValueError("no collectors selected")

    try:
        await asyncio.sleep(duration_seconds)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    event_files = (
        list(output_root.rglob("events.jsonl")) if output_root.exists() else []
    )
    manifest: dict[str, object] = {
        "status": "ok" if event_files else "no_events",
        "sources": list(sources),
        "duration_seconds": duration_seconds,
        "event_files": [str(path) for path in event_files],
        "errors": errors,
    }
    write_json(output_root / "collection_manifest.json", manifest)
    return manifest
