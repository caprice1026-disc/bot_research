from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from btc5m.clock import ReceiveStamp, capture_receive_stamp

MessageStream = AsyncIterator[Mapping[str, Any]]
Connect = Callable[[], MessageStream | Awaitable[MessageStream]]
MessageHandler = Callable[[Mapping[str, Any], ReceiveStamp], Awaitable[None]]
ErrorHandler = Callable[[str, Exception], None]
ConnectionHook = Callable[[str, str], None]
DisconnectHook = Callable[[str, str, str], None]


async def reconnect_forever(
    source: str,
    connect: Connect,
    handle: MessageHandler,
    stop: asyncio.Event,
    *,
    reconnect_base_seconds: float = 1.0,
    reconnect_max_seconds: float = 30.0,
    on_error: ErrorHandler | None = None,
    connection_id_factory: Callable[[], str] | None = None,
    on_connect: ConnectionHook | None = None,
    on_disconnect: DisconnectHook | None = None,
) -> None:
    delay = max(0.0, reconnect_base_seconds)
    while not stop.is_set():
        connection_id = (
            connection_id_factory() if connection_id_factory is not None else uuid4().hex
        )
        if on_connect is not None:
            on_connect(source, connection_id)
        disconnect_notified = False
        try:
            stream = connect()
            if inspect.isawaitable(stream):
                stream = await stream
            async for message in stream:
                await handle(message, capture_receive_stamp())
                # A healthy message proves the path recovered; do not carry a
                # previous outage's exponential delay into the next outage.
                delay = max(0.0, reconnect_base_seconds)
            if stop.is_set():
                if on_disconnect is not None:
                    on_disconnect(source, connection_id, "stop")
                return
            raise ConnectionError("stream ended without stop signal")
        except asyncio.CancelledError:
            if on_disconnect is not None and not disconnect_notified:
                on_disconnect(source, connection_id, "cancelled")
            raise
        except Exception as exc:
            if stop.is_set():
                if on_disconnect is not None and not disconnect_notified:
                    on_disconnect(
                        source,
                        connection_id,
                        f"{type(exc).__name__}: {exc}",
                    )
                return
            if on_error is not None:
                on_error(source, exc)
            if on_disconnect is not None and not disconnect_notified:
                on_disconnect(
                    source,
                    connection_id,
                    f"{type(exc).__name__}: {exc}",
                )
                disconnect_notified = True
            if delay > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
            delay = min(reconnect_max_seconds, max(delay * 2, reconnect_base_seconds))
