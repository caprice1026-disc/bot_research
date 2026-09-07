from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    MutableMapping,
)
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
from btc5m.coverage import CoverageTracker
from btc5m.events import RawEvent
from btc5m.io import write_json
from btc5m.market_master import write_market_master
from btc5m.storage import append_jsonl, append_jsonl_rows, partition_path

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


def _log_connection(
    path: Path,
    *,
    source: str,
    connection_id: str,
    event: str,
    reason: str | None = None,
    dropped: int | None = None,
) -> None:
    row: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "source": source,
        "connection_id": connection_id,
        "event": event,
    }
    if reason is not None:
        row["reason"] = reason
    if dropped is not None:
        row["dropped"] = dropped
    append_jsonl(path, row)


class _SourceEventWriter:
    def __init__(
        self,
        *,
        source: str,
        output_root: Path,
        event_counts: MutableMapping[str, int],
        event_counts_by_type: MutableMapping[str, int],
        event_paths: set[Path],
        last_received_monotonic: MutableMapping[str, float],
        last_received_by_channel: MutableMapping[str, float],
        coverage_tracker: CoverageTracker,
        error_sink: Callable[[str], None] | None,
        queue_maxsize: int,
    ) -> None:
        self.source = source
        self.output_root = output_root
        self.event_counts = event_counts
        self.event_counts_by_type = event_counts_by_type
        self.event_paths = event_paths
        self.last_received_monotonic = last_received_monotonic
        self.last_received_by_channel = last_received_by_channel
        self.coverage_tracker = coverage_tracker
        self.error_sink = error_sink
        self.queue: asyncio.Queue[tuple[RawEvent, str | None] | None] = asyncio.Queue(
            maxsize=max(1, queue_maxsize)
        )
        self.task: asyncio.Task[None] | None = None
        self.failure: Exception | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def submit(self, event: RawEvent, connection_id: str | None) -> None:
        if self.failure is not None:
            raise RuntimeError(f"event writer failed for {self.source}") from self.failure
        await self.queue.put((event, connection_id))

    async def _run(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            batch = [item]
            while len(batch) < 1_000:
                try:
                    next_item = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if next_item is None:
                    # Put the sentinel back after this batch has been flushed.
                    self.queue.task_done()
                    await self.queue.put(None)
                    break
                batch.append(next_item)
            try:
                rows_by_path: dict[Path, list[dict[str, object]]] = defaultdict(list)
                prepared: list[tuple[RawEvent, str | None, Path]] = []
                for event, connection_id in batch:
                    if event.local_receive_ts is None:
                        raise ValueError("live collector event is missing local_receive_ts")
                    received_ts = event.local_receive_ts
                    resolved_connection = connection_id or event.connection_id
                    if resolved_connection and event.connection_id != resolved_connection:
                        event = replace(event, connection_id=resolved_connection)
                    path = partition_path(
                        self.output_root,
                        event.source,
                        received_ts,
                    )
                    prepared.append((event, resolved_connection, path))
                    rows_by_path[path].append(event.to_row())
                for path, rows in rows_by_path.items():
                    await asyncio.to_thread(append_jsonl_rows, path, rows)
                    self.event_paths.add(path)
                for event, connection_id, _ in prepared:
                    self.event_counts[event.source] = self.event_counts.get(event.source, 0) + 1
                    channel_key = f"{event.source}/{event.event_type}"
                    self.event_counts_by_type[channel_key] = (
                        self.event_counts_by_type.get(channel_key, 0) + 1
                    )
                    receive_monotonic = (
                        event.local_monotonic_ns / 1_000_000_000
                        if event.local_monotonic_ns is not None
                        else time.monotonic()
                    )
                    self.last_received_monotonic[event.source] = receive_monotonic
                    self.last_received_by_channel[channel_key] = receive_monotonic
                    self.coverage_tracker.observe(
                        event.to_row(), connection_id=connection_id
                    )
            except Exception as exc:
                self.failure = exc
                if self.error_sink is not None:
                    self.error_sink(
                        f"{self.source}: event writer failed: {type(exc).__name__}: {exc}"
                    )
                # Mark the current batch done and discard queued items so close()
                # cannot hang after a disk failure.
                while not self.queue.empty():
                    self.queue.get_nowait()
                    self.queue.task_done()
                return
            finally:
                for _ in batch:
                    self.queue.task_done()

    async def close(self) -> None:
        if self.task is None:
            return
        await self.queue.join()
        await self.queue.put(None)
        await self.task
        if self.failure is not None:
            raise RuntimeError(f"event writer failed for {self.source}") from self.failure


class EventWriterPool:
    """Persist each source on its own queue and retain receive-time metadata."""

    def __init__(
        self,
        *,
        output_root: Path,
        sources: Iterable[str],
        event_counts: MutableMapping[str, int],
        event_counts_by_type: MutableMapping[str, int],
        event_paths: set[Path],
        last_received_monotonic: MutableMapping[str, float],
        last_received_by_channel: MutableMapping[str, float],
        coverage_tracker: CoverageTracker,
        error_sink: Callable[[str], None] | None,
        queue_maxsize: int = 4_096,
    ) -> None:
        self.writers = {
            source: _SourceEventWriter(
                source=source,
                output_root=output_root,
                event_counts=event_counts,
                event_counts_by_type=event_counts_by_type,
                event_paths=event_paths,
                last_received_monotonic=last_received_monotonic,
                last_received_by_channel=last_received_by_channel,
                coverage_tracker=coverage_tracker,
                error_sink=error_sink,
                queue_maxsize=queue_maxsize,
            )
            for source in sources
        }

    async def start(self) -> None:
        for writer in self.writers.values():
            await writer.start()

    async def submit(
        self,
        events: Iterable[RawEvent],
        *,
        connection_id: str | None = None,
    ) -> None:
        grouped: dict[str, list[RawEvent]] = defaultdict(list)
        for event in events:
            grouped[event.source].append(event)
        for source, source_events in grouped.items():
            writer = self.writers.get(source)
            if writer is None:
                raise ValueError(f"writer was not configured for source {source}")
            for event in source_events:
                await writer.submit(event, connection_id)

    async def close(self) -> None:
        failures: list[Exception] = []
        for writer in self.writers.values():
            try:
                await writer.close()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise failures[0]


async def _write_events(output_root: Path, events: Iterable[RawEvent]) -> None:
    await _write_events_with_metrics(output_root, events)


async def _write_events_with_metrics(
    output_root: Path,
    events: Iterable[RawEvent],
    event_counts: MutableMapping[str, int] | None = None,
    event_paths: set[Path] | None = None,
    last_received_monotonic: MutableMapping[str, float] | None = None,
    *,
    writers: EventWriterPool | None = None,
    connection_id: str | None = None,
) -> None:
    if writers is not None:
        await writers.submit(events, connection_id=connection_id)
        return
    rows_by_path: dict[Path, list[dict[str, object]]] = defaultdict(list)
    prepared: list[RawEvent] = []
    for event in events:
        if event.local_receive_ts is None:
            raise ValueError("live collector event is missing local_receive_ts")
        path = partition_path(output_root, event.source, event.local_receive_ts)
        prepared.append(event)
        rows_by_path[path].append(event.to_row())
    for path, rows in rows_by_path.items():
        append_jsonl_rows(path, rows)
    for event in prepared:
        if event_counts is not None:
            event_counts[event.source] = event_counts.get(event.source, 0) + 1
        if event_paths is not None:
            event_paths.add(
                partition_path(output_root, event.source, event.local_receive_ts)  # type: ignore[arg-type]
            )
        if last_received_monotonic is not None:
            last_received_monotonic[event.source] = (
                event.local_monotonic_ns / 1_000_000_000
                if event.local_monotonic_ns is not None
                else time.monotonic()
            )


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
    writers: EventWriterPool | None = None,
    connection_log_path: Path | None = None,
) -> None:
    current_connection_id: str | None = None

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
            writers=writers,
            connection_id=current_connection_id,
        )

    def on_connect(_: str, connection_id: str) -> None:
        nonlocal current_connection_id
        current_connection_id = connection_id
        if connection_log_path is not None:
            _log_connection(
                connection_log_path,
                source=source,
                connection_id=connection_id,
                event="connected",
            )

    def on_disconnect(_: str, connection_id: str, reason: str) -> None:
        if connection_log_path is not None:
            _log_connection(
                connection_log_path,
                source=source,
                connection_id=connection_id,
                event="disconnected",
                reason=reason,
            )

    def on_runner_error(error_source: str, error: Exception) -> None:
        if connection_log_path is not None and current_connection_id is not None:
            _log_connection(
                connection_log_path,
                source=error_source,
                connection_id=current_connection_id,
                event="reconnect_scheduled",
                reason=f"{type(error).__name__}: {error}",
            )
        if error_sink is not None:
            error_sink(f"{error_source}: {type(error).__name__}: {error}")

    await reconnect_forever(
        source,
        connect,
        handle,
        stop,
        on_error=on_runner_error,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
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
    writers: EventWriterPool | None = None,
    connection_log_path: Path | None = None,
    sdk_drop_counts: MutableMapping[str, int] | None = None,
) -> None:
    from polymarket import AsyncPublicClient

    async with AsyncPublicClient() as client:
        client_api: Any = client
        refresh_seconds = max(0.1, market_refresh_seconds)
        connection_log = connection_log_path or output_root / "logs" / "connections.jsonl"

        def report_error(source: str, exc: Exception) -> None:
            if error_sink is not None:
                error_sink(f"{source}: {type(exc).__name__}: {exc}")

        def log(source: str, connection_id: str, event: str, reason: str | None = None, dropped: int | None = None) -> None:
            _log_connection(
                connection_log,
                source=source,
                connection_id=connection_id,
                event=event,
                reason=reason,
                dropped=dropped,
            )

        def record_sdk_drop(
            source: str,
            connection_id: str,
            stream: object,
            previous: int,
        ) -> int:
            dropped = int(getattr(stream, "dropped", 0) or 0)
            if dropped > previous:
                if sdk_drop_counts is not None:
                    sdk_drop_counts[source] = dropped
                log(source, connection_id, "sdk_drop", dropped=dropped)
                return dropped
            return previous

        async def wait_refresh() -> None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=refresh_seconds)
            except TimeoutError:
                pass

        async def consume_chainlink() -> None:
            while not stop.is_set():
                connection_id = f"chainlink-{uuid4().hex}"
                log("chainlink", connection_id, "connected")
                last_dropped = 0
                stream: Any = None
                try:
                    async with await client_api.subscribe(build_chainlink_specs()) as stream:
                        iterator = stream.__aiter__()
                        while not stop.is_set():
                            try:
                                event = await asyncio.wait_for(iterator.__anext__(), 1.0)
                            except TimeoutError:
                                continue
                            except StopAsyncIteration as exc:
                                raise ConnectionError(
                                    "Chainlink stream ended before stop"
                                ) from exc
                            received = ReceiveStamp(
                                datetime.now(timezone.utc),
                                time.monotonic_ns(),
                            )
                            mapping = _model_mapping(event)
                            body = mapping.get("payload")
                            window = (
                                int(body.get("window_seconds", 60))
                                if isinstance(body, Mapping)
                                else 60
                            )
                            await _write_events_with_metrics(
                                output_root,
                                parse_chainlink_message(mapping, received, window),
                                event_counts,
                                event_paths,
                                last_received_monotonic,
                                writers=writers,
                                connection_id=connection_id,
                            )
                            last_dropped = record_sdk_drop(
                                "chainlink", connection_id, stream, last_dropped
                            )
                    last_dropped = record_sdk_drop(
                        "chainlink", connection_id, stream, last_dropped
                    )
                    log("chainlink", connection_id, "disconnected", "stop")
                    return
                except asyncio.CancelledError:
                    if stream is not None:
                        record_sdk_drop("chainlink", connection_id, stream, last_dropped)
                    log("chainlink", connection_id, "disconnected", "cancelled")
                    raise
                except Exception as exc:
                    if stream is not None:
                        last_dropped = record_sdk_drop(
                            "chainlink", connection_id, stream, last_dropped
                        )
                    report_error("chainlink", exc)
                    log(
                        "chainlink",
                        connection_id,
                        "disconnected",
                        f"{type(exc).__name__}: {exc}",
                    )
                    if not stop.is_set():
                        log(
                            "chainlink",
                            connection_id,
                            "reconnect_scheduled",
                            f"{type(exc).__name__}: {exc}",
                        )
                        await wait_refresh()

        async def consume_markets() -> None:
            known_markets: dict[str, MarketIdentity] = {}
            while not stop.is_set():
                token_ids: list[str] = []
                try:
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
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    report_error("polymarket_market_discovery", exc)
                    discovery_id = f"polymarket-discovery-{uuid4().hex}"
                    log(
                        "polymarket_market_discovery",
                        discovery_id,
                        "error",
                        f"{type(exc).__name__}: {exc}",
                    )
                    await wait_refresh()
                    continue
                if not token_ids:
                    await wait_refresh()
                    continue
                connection_id = f"polymarket-{uuid4().hex}"
                log("polymarket", connection_id, "connected")
                last_dropped = 0
                stream: Any = None
                try:
                    async with await client_api.subscribe(build_market_specs(token_ids)) as stream:
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
                            except StopAsyncIteration as exc:
                                raise ConnectionError(
                                    "Polymarket market stream ended before refresh"
                                ) from exc
                            received = ReceiveStamp(
                                datetime.now(timezone.utc),
                                time.monotonic_ns(),
                            )
                            mapping = _model_mapping(event)
                            await _write_events_with_metrics(
                                output_root,
                                parse_polymarket_message(mapping, received),
                                event_counts,
                                event_paths,
                                last_received_monotonic,
                                writers=writers,
                                connection_id=connection_id,
                            )
                            last_dropped = record_sdk_drop(
                                "polymarket", connection_id, stream, last_dropped
                            )
                    last_dropped = record_sdk_drop(
                        "polymarket", connection_id, stream, last_dropped
                    )
                    log("polymarket", connection_id, "disconnected", "refresh")
                except asyncio.CancelledError:
                    if stream is not None:
                        record_sdk_drop("polymarket", connection_id, stream, last_dropped)
                    log("polymarket", connection_id, "disconnected", "cancelled")
                    raise
                except Exception as exc:
                    if stream is not None:
                        last_dropped = record_sdk_drop(
                            "polymarket", connection_id, stream, last_dropped
                        )
                    report_error("polymarket", exc)
                    log(
                        "polymarket",
                        connection_id,
                        "disconnected",
                        f"{type(exc).__name__}: {exc}",
                    )
                    await wait_refresh()

        workers: list[asyncio.Task[None]] = []
        if include_chainlink:
            workers.append(asyncio.create_task(consume_chainlink()))
        if include_market:
            workers.append(asyncio.create_task(consume_markets()))
        if workers:
            await asyncio.gather(*workers)


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


def stale_channels(
    last_received_by_channel: Mapping[str, float],
    *,
    now_monotonic: float,
    max_stale_seconds: float,
) -> list[str]:
    return sorted(
        channel
        for channel, last_received in last_received_by_channel.items()
        if now_monotonic - last_received > max_stale_seconds
    )


def expected_channels(sources: tuple[str, ...]) -> tuple[str, ...]:
    expected: list[str] = []
    if "binance" in sources:
        expected.extend(("binance/agg_trade", "binance/book_ticker"))
    if "coinbase" in sources:
        expected.extend(("coinbase/ticker", "coinbase/market_trade"))
    if "hyperliquid" in sources:
        expected.extend(("hyperliquid/trade", "hyperliquid/bbo"))
    if "chainlink" in sources:
        expected.extend(("chainlink/chainlink_twap_30", "chainlink/chainlink_twap_60"))
    return tuple(expected)


def missing_channels(
    sources: tuple[str, ...],
    event_counts_by_type: Mapping[str, int],
) -> list[str]:
    return [
        channel
        for channel in expected_channels(sources)
        if event_counts_by_type.get(channel, 0) <= 0
    ]


async def collect_public(
    sources: tuple[str, ...],
    output_root: Path,
    duration_seconds: int,
    max_stale_seconds: float = 90.0,
    gap_threshold_seconds: float = 5.0,
) -> dict[str, object]:
    if duration_seconds < 1:
        raise ValueError("duration_seconds must be at least 1")
    if max_stale_seconds <= 0:
        raise ValueError("max_stale_seconds must be positive")
    if gap_threshold_seconds <= 0:
        raise ValueError("gap_threshold_seconds must be positive")
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    errors: list[str] = []
    event_counts: dict[str, int] = {}
    event_counts_by_type: dict[str, int] = {}
    event_paths: set[Path] = set()
    last_received_monotonic: dict[str, float] = {}
    last_received_by_channel: dict[str, float] = {}
    sdk_drop_counts: dict[str, int] = {}
    coverage_tracker = CoverageTracker(max_gap_seconds=gap_threshold_seconds)
    connection_log_path = output_root / "logs" / "connections.jsonl"
    writers = EventWriterPool(
        output_root=output_root,
        sources=sources,
        event_counts=event_counts,
        event_counts_by_type=event_counts_by_type,
        event_paths=event_paths,
        last_received_monotonic=last_received_monotonic,
        last_received_by_channel=last_received_by_channel,
        coverage_tracker=coverage_tracker,
        error_sink=lambda message: errors.append(message) if len(errors) < 100 else None,
    )
    await writers.start()

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
                        writers=writers,
                        connection_log_path=connection_log_path,
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
                        writers=writers,
                        connection_log_path=connection_log_path,
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
                        writers=writers,
                        connection_log_path=connection_log_path,
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
                        writers=writers,
                        connection_log_path=connection_log_path,
                        sdk_drop_counts=sdk_drop_counts,
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
        try:
            await writers.close()
        except Exception as exc:
            record_error(f"writer: {type(exc).__name__}: {exc}")

    finished_monotonic = time.monotonic()
    finished_ts = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    coverage_tracker.finish(finished_ts)
    gap_path = output_root / "logs" / "gaps.jsonl"
    append_jsonl_rows(gap_path, coverage_tracker.to_dicts())

    event_files = sorted(event_paths)
    missing_sources = [source for source in sources if event_counts.get(source, 0) <= 0]
    stale = stale_sources(
        sources,
        last_received_monotonic,
        now_monotonic=finished_monotonic,
        max_stale_seconds=max_stale_seconds,
    )
    stale_channel_list = stale_channels(
        last_received_by_channel,
        now_monotonic=finished_monotonic,
        max_stale_seconds=max_stale_seconds,
    )
    missing_channel_list = missing_channels(sources, event_counts_by_type)
    manifest: dict[str, object] = {
        "status": collection_status(
            sources,
            event_counts,
            errors,
            [*stale, *stale_channel_list, *missing_channel_list],
        ),
        "sources": list(sources),
        "duration_seconds": duration_seconds,
        "event_files": [str(path) for path in event_files],
        "event_counts": dict(sorted(event_counts.items())),
        "missing_sources": missing_sources,
        "stale_sources": stale,
        "stale_channels": stale_channel_list,
        "missing_channels": missing_channel_list,
        "max_stale_seconds": max_stale_seconds,
        "gap_threshold_seconds": gap_threshold_seconds,
        "last_receive_age_seconds": {
            source: round(finished_monotonic - received, 3)
            for source, received in sorted(last_received_monotonic.items())
        },
        "event_counts_by_type": dict(sorted(event_counts_by_type.items())),
        "sdk_drop_counts": dict(sorted(sdk_drop_counts.items())),
        "connection_log": str(connection_log_path),
        "gap_log": str(gap_path),
        "gap_count": len(coverage_tracker.gaps),
        "coverage_gaps": coverage_tracker.to_dicts(),
        "errors": errors,
    }
    write_json(output_root / "collection_manifest.json", manifest)
    return manifest
