from __future__ import annotations

import asyncio
import json
import time
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    MutableMapping,
)
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
    MarketIdentity,
    active_token_ids,
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
    await _write_events_with_metrics(output_root, events)


async def _write_events_with_metrics(
    output_root: Path,
    events: Iterable[RawEvent],
    event_counts: MutableMapping[str, int] | None = None,
    event_paths: set[Path] | None = None,
    last_received_monotonic: MutableMapping[str, float] | None = None,
) -> None:
    for event in events:
        if event.local_receive_ts is None:
            raise ValueError("live collector event is missing local_receive_ts")
        path = partition_path(output_root, event.source, event.local_receive_ts)
        append_jsonl(path, event.to_row())
        if event_counts is not None:
            event_counts[event.source] = event_counts.get(event.source, 0) + 1
        if event_paths is not None:
            event_paths.add(path)
        if last_received_monotonic is not None:
            last_received_monotonic[event.source] = time.monotonic()


async def run_external_source(
    *,
    source: str,
    uri: str,
    parser: Callable[[Mapping[str, Any], ReceiveStamp], list[RawEvent]],
    output_root: Path,
    stop: asyncio.Event,
    subscribe_messages: Iterable[Mapping[str, object]] = (),
    event_counts: MutableMapping[str, int] | None = None,
    event_paths: set[Path] | None = None,
    last_received_monotonic: MutableMapping[str, float] | None = None,
    error_sink: Callable[[str], None] | None = None,
) -> None:
    async def connect() -> AsyncIterator[Mapping[str, Any]]:
        async for message in websocket_messages(uri, subscribe_messages):
            yield message

    async def handle(message: Mapping[str, Any], received: ReceiveStamp) -> None:
        await _write_events_with_metrics(
            output_root,
            parser(message, received),
            event_counts,
            event_paths,
            last_received_monotonic,
        )

    await reconnect_forever(
        source,
        connect,
        handle,
        stop,
        on_error=(
            None
            if error_sink is None
            else lambda error_source, error: error_sink(
                f"{error_source}: {type(error).__name__}: {error}"
            )
        ),
    )


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
    event_counts: MutableMapping[str, int] | None = None,
    event_paths: set[Path] | None = None,
    last_received_monotonic: MutableMapping[str, float] | None = None,
    error_sink: Callable[[str], None] | None = None,
    market_refresh_seconds: float = 30.0,
    market_grace_seconds: int = 60,
) -> None:
    from polymarket import AsyncPublicClient

    async with AsyncPublicClient() as client:
        known_markets: dict[str, MarketIdentity] = {}
        refresh_seconds = max(0.1, market_refresh_seconds)
        while not stop.is_set():
            try:
                token_ids: list[str] = []
                if include_market:
                    discovered = await discover_btc_5m_markets(client)
                    known_markets.update(
                        {market.condition_id: market for market in discovered}
                    )
                    write_market_master(
                        output_root / "market_master" / "markets.parquet",
                        known_markets.values(),
                    )
                    token_ids = active_token_ids(
                        tuple(known_markets.values()),
                        now=datetime.now(timezone.utc),
                        grace_seconds=market_grace_seconds,
                    )
                specs: list[Any] = []
                if include_market and token_ids:
                    specs.extend(build_market_specs(token_ids))
                if include_chainlink:
                    specs.extend(build_chainlink_specs())
                if not specs:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=refresh_seconds)
                    except TimeoutError:
                        pass
                    continue
                async with await client.subscribe(specs) as stream:
                    iterator = stream.__aiter__()
                    refresh_deadline = time.monotonic() + refresh_seconds
                    while not stop.is_set():
                        timeout = refresh_deadline - time.monotonic()
                        if timeout <= 0:
                            break
                        try:
                            event = await asyncio.wait_for(
                                iterator.__anext__(), timeout
                            )
                        except TimeoutError:
                            break
                        except StopAsyncIteration:
                            if error_sink is not None and not stop.is_set():
                                error_sink(
                                    "polymarket/chainlink: stream ended before refresh"
                                )
                            break
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
                        await _write_events_with_metrics(
                            output_root,
                            events,
                            event_counts,
                            event_paths,
                            last_received_monotonic,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if error_sink is not None:
                    error_sink(f"polymarket/chainlink: {type(exc).__name__}: {exc}")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=refresh_seconds)
                except TimeoutError:
                    pass


def collection_status(
    sources: tuple[str, ...],
    event_counts: Mapping[str, int],
    errors: Iterable[str],
    stale: Iterable[str] = (),
) -> str:
    error_list = tuple(errors)
    stale_list = tuple(stale)
    if not any(event_counts.get(source, 0) > 0 for source in sources):
        return "error" if error_list else "no_events"
    if (
        error_list
        or stale_list
        or any(event_counts.get(source, 0) <= 0 for source in sources)
    ):
        return "partial"
    return "ok"


def stale_sources(
    sources: tuple[str, ...],
    last_received_monotonic: Mapping[str, float],
    *,
    now_monotonic: float,
    max_stale_seconds: float,
) -> list[str]:
    return [
        source
        for source in sources
        if (last_received := last_received_monotonic.get(source)) is not None
        and now_monotonic - last_received > max_stale_seconds
    ]


async def collect_public(
    sources: tuple[str, ...],
    output_root: Path,
    duration_seconds: int,
    max_stale_seconds: float = 90.0,
) -> dict[str, object]:
    if duration_seconds < 1:
        raise ValueError("duration_seconds must be at least 1")
    if max_stale_seconds <= 0:
        raise ValueError("max_stale_seconds must be positive")
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    errors: list[str] = []
    event_counts: dict[str, int] = {}
    event_paths: set[Path] = set()
    last_received_monotonic: dict[str, float] = {}

    def record_error(message: str) -> None:
        if len(errors) < 100:
            errors.append(message)

    async def guarded(name: str, operation: Awaitable[None]) -> None:
        try:
            await operation
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record_error(f"{name}: {type(exc).__name__}: {exc}")
        else:
            if not stop.is_set():
                record_error(f"{name}: collector stopped without stop signal")

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
                        event_counts=event_counts,
                        event_paths=event_paths,
                        last_received_monotonic=last_received_monotonic,
                        error_sink=record_error,
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
                        event_counts=event_counts,
                        event_paths=event_paths,
                        last_received_monotonic=last_received_monotonic,
                        error_sink=record_error,
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
                        event_counts=event_counts,
                        event_paths=event_paths,
                        last_received_monotonic=last_received_monotonic,
                        error_sink=record_error,
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
                        event_counts=event_counts,
                        event_paths=event_paths,
                        last_received_monotonic=last_received_monotonic,
                        error_sink=record_error,
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

    event_files = sorted(event_paths)
    missing_sources = [source for source in sources if event_counts.get(source, 0) <= 0]
    finished_monotonic = time.monotonic()
    stale = stale_sources(
        sources,
        last_received_monotonic,
        now_monotonic=finished_monotonic,
        max_stale_seconds=max_stale_seconds,
    )
    manifest: dict[str, object] = {
        "status": collection_status(sources, event_counts, errors, stale),
        "sources": list(sources),
        "duration_seconds": duration_seconds,
        "event_files": [str(path) for path in event_files],
        "event_counts": dict(sorted(event_counts.items())),
        "missing_sources": missing_sources,
        "stale_sources": stale,
        "max_stale_seconds": max_stale_seconds,
        "last_receive_age_seconds": {
            source: round(finished_monotonic - received, 3)
            for source, received in sorted(last_received_monotonic.items())
        },
        "errors": errors,
    }
    write_json(output_root / "collection_manifest.json", manifest)
    return manifest
