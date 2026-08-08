from __future__ import annotations

import time
from collections.abc import Iterator

from base_lp.data.events import (
    BURN_TOPIC,
    COLLECT_TOPIC,
    MINT_TOPIC,
    SWAP_TOPIC,
    decode_liquidity_log,
    decode_swap_log,
)
from base_lp.data.normalize import log_record_from_rpc
from base_lp.data.rpc import JsonRpcError, hex_to_int, normalize_address
from base_lp.schemas import LogRecord


class _RequestPacer:
    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds < 0:
            raise ValueError("request interval must be non-negative")
        self.interval_seconds = interval_seconds

    def pause(self) -> None:
        if self.interval_seconds:
            time.sleep(self.interval_seconds)


def timestamp_to_block(
    rpc,
    target_timestamp: int,
    latest_block: int,
    request_interval_seconds: float = 0.0,
) -> int:
    if target_timestamp < 0 or latest_block < 0:
        raise ValueError("timestamp and latest block must be non-negative")
    pacer = _RequestPacer(request_interval_seconds)
    latest = rpc.block_by_number(latest_block, False)
    pacer.pause()
    if latest is None:
        raise ValueError(f"latest block {latest_block} is unavailable")
    if hex_to_int(str(latest["timestamp"])) < target_timestamp:
        raise ValueError("target timestamp is newer than latest block")
    latest_timestamp = hex_to_int(str(latest["timestamp"]))
    estimated = latest_block - max(0, (latest_timestamp - target_timestamp) // 2)
    low = max(0, estimated - 100_000)
    high = min(latest_block, estimated + 100_000)

    def block_timestamp(block_number: int) -> int:
        block = rpc.block_by_number(block_number, False)
        pacer.pause()
        if block is None:
            raise ValueError(f"block {block_number} is unavailable")
        return hex_to_int(str(block["timestamp"]))

    try:
        low_timestamp = block_timestamp(low)
        high_timestamp = block_timestamp(high)
        step = max(1, high - low)
        while low > 0 and low_timestamp >= target_timestamp:
            high = low
            high_timestamp = low_timestamp
            low = max(0, low - step)
            low_timestamp = block_timestamp(low)
            step *= 2
        step = max(1, high - low)
        while high < latest_block and high_timestamp < target_timestamp:
            low = high
            low_timestamp = high_timestamp
            high = min(latest_block, high + step)
            high_timestamp = block_timestamp(high)
            step *= 2
    except JsonRpcError as exc:
        raise ValueError(
            "archive_data_required: provider cannot read the historical block window; "
            "use an archive-capable Base RPC"
        ) from exc
    while low < high:
        middle = (low + high) // 2
        if block_timestamp(middle) >= target_timestamp:
            high = middle
        else:
            low = middle + 1
    return low


def _fetch_block_timestamps(
    rpc,
    block_numbers: list[int],
    pacer: _RequestPacer,
    batch_enabled: bool,
) -> tuple[dict[int, int], bool]:
    timestamps: dict[int, int] = {}
    for offset in range(0, len(block_numbers), 100):
        batch = block_numbers[offset : offset + 100]
        calls = [("eth_getBlockByNumber", [hex(block_number), False]) for block_number in batch]
        blocks = None
        if batch_enabled and hasattr(rpc, "batch_request"):
            try:
                blocks = rpc.batch_request(calls)
                pacer.pause()
                if len(blocks) != len(batch):
                    raise ValueError("batch response length does not match request length")
            except (JsonRpcError, TypeError, ValueError):
                batch_enabled = False
                pacer.pause()
        if blocks is None:
            blocks = []
            for method, params in calls:
                blocks.append(rpc.request(method, params))
                pacer.pause()
        for block_number, block in zip(batch, blocks):
            if not block:
                raise ValueError(f"missing block {block_number} while normalizing logs")
            timestamps[block_number] = hex_to_int(str(block["timestamp"]))
    return timestamps, batch_enabled


def iter_log_chunks(
    rpc,
    pool_address: str,
    from_block: int,
    to_block: int,
    chunk_size: int = 2_000,
    request_interval_seconds: float = 0.0,
) -> Iterator[list[LogRecord]]:
    if from_block > to_block:
        raise ValueError("from_block must not exceed to_block")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    pool_address = normalize_address(pool_address)
    pacer = _RequestPacer(request_interval_seconds)
    batch_enabled = hasattr(rpc, "batch_request")
    for chunk_start in range(from_block, to_block + 1, chunk_size):
        chunk_end = min(chunk_start + chunk_size - 1, to_block)
        raw_logs = rpc.request(
            "eth_getLogs",
            [
                {
                    "address": pool_address,
                    "fromBlock": hex(chunk_start),
                    "toBlock": hex(chunk_end),
                }
            ],
        )
        pacer.pause()
        block_numbers = sorted({hex_to_int(str(raw["blockNumber"])) for raw in raw_logs})
        timestamps, batch_enabled = _fetch_block_timestamps(rpc, block_numbers, pacer, batch_enabled)
        records = [
            log_record_from_rpc(raw, timestamps[hex_to_int(str(raw["blockNumber"]))])
            for raw in raw_logs
        ]
        records.sort(key=lambda record: record.stable_key)
        yield records


def collect_logs(
    rpc,
    pool_address: str,
    from_block: int,
    to_block: int,
    chunk_size: int = 2_000,
    request_interval_seconds: float = 0.0,
) -> list[LogRecord]:
    records: list[LogRecord] = []
    for chunk in iter_log_chunks(
        rpc,
        pool_address,
        from_block,
        to_block,
        chunk_size=chunk_size,
        request_interval_seconds=request_interval_seconds,
    ):
        records.extend(chunk)
    records.sort(key=lambda record: record.stable_key)
    return records


def _safe_int(value: int) -> int | str:
    return value if -(1 << 63) <= value < 1 << 63 else str(value)


def normalize_event_rows(records: list[LogRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in records:
        base = {
            "block_number": record.block_number,
            "transaction_index": record.transaction_index,
            "log_index": record.log_index,
            "block_hash": record.block_hash,
            "transaction_hash": record.transaction_hash,
            "address": record.address,
            "timestamp": record.timestamp,
            "topics": record.topics,
            "data": record.data,
        }
        if record.topics and record.topics[0].lower() == SWAP_TOPIC.lower():
            event = decode_swap_log(record)
            rows.append(
                {
                    **base,
                    "event_type": "swap",
                    "sender": event.sender,
                    "recipient": event.recipient,
                    "amount0": _safe_int(event.amount0),
                    "amount1": _safe_int(event.amount1),
                    "sqrt_price_x96": str(event.sqrt_price_x96),
                    "liquidity": str(event.liquidity),
                    "tick": event.tick,
                }
            )
        elif record.topics and record.topics[0].lower() in {
            MINT_TOPIC.lower(),
            BURN_TOPIC.lower(),
            COLLECT_TOPIC.lower(),
        }:
            event = decode_liquidity_log(record)
            rows.append(
                {
                    **base,
                    "event_type": event.event_type,
                    "owner": event.owner,
                    "tick_lower": event.tick_lower,
                    "tick_upper": event.tick_upper,
                    "amount": str(event.amount),
                    "amount0": str(event.amount0),
                    "amount1": str(event.amount1),
                }
            )
        else:
            rows.append({**base, "event_type": "unknown"})
    return rows
