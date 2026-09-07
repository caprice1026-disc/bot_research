from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from btc5m.clock import ReceiveStamp, capture_receive_stamp

MessageStream = AsyncIterator[Mapping[str, Any]]
Connect = Callable[[], MessageStream | Awaitable[MessageStream]]
MessageHandler = Callable[[Mapping[str, Any], ReceiveStamp], Awaitable[None]]
ErrorHandler = Callable[[str, Exception], None]


async def reconnect_forever(
    source: str,
    connect: Connect,
    handle: MessageHandler,
    stop: asyncio.Event,
    *,
    reconnect_base_seconds: float = 1.0,
    reconnect_max_seconds: float = 30.0,
    on_error: ErrorHandler | None = None,
) -> None:
    delay = max(0.0, reconnect_base_seconds)
    while not stop.is_set():
        try:
            stream = connect()
            if inspect.isawaitable(stream):
                stream = await stream
            async for message in stream:
                await handle(message, capture_receive_stamp())
            if stop.is_set():
                return
            raise ConnectionError("stream ended without stop signal")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if stop.is_set():
                return
            if on_error is not None:
                on_error(source, exc)
            if delay > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
            delay = min(reconnect_max_seconds, max(delay * 2, reconnect_base_seconds))
