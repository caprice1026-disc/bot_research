from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LogRecord:
    block_number: int
    transaction_index: int
    log_index: int
    block_hash: str
    transaction_hash: str
    address: str
    topics: list[str]
    data: str
    timestamp: int

    @property
    def stable_key(self) -> tuple[int, int, int]:
        return (self.block_number, self.transaction_index, self.log_index)


@dataclass(frozen=True)
class SwapEvent:
    block_number: int
    transaction_index: int
    log_index: int
    block_hash: str
    transaction_hash: str
    address: str
    timestamp: int
    sender: str
    recipient: str
    amount0: int
    amount1: int
    sqrt_price_x96: int
    liquidity: int
    tick: int

    @property
    def stable_key(self) -> tuple[int, int, int]:
        return (self.block_number, self.transaction_index, self.log_index)


@dataclass(frozen=True)
class LiquidityEvent:
    event_type: str
    block_number: int
    transaction_index: int
    log_index: int
    block_hash: str
    transaction_hash: str
    address: str
    timestamp: int
    owner: str
    tick_lower: int
    tick_upper: int
    amount: int
    amount0: int
    amount1: int

    @property
    def stable_key(self) -> tuple[int, int, int]:
        return (self.block_number, self.transaction_index, self.log_index)


@dataclass(frozen=True)
class PoolMetadata:
    chain_id: int
    pool_address: str
    token0: str
    token1: str
    token0_decimals: int
    token1_decimals: int
    fee_tier: int
    tick_spacing: int
    creation_block: int


@dataclass(frozen=True)
class DatasetManifest:
    status: str
    source: str
    chain_id: int
    pool: PoolMetadata
    block_start: int
    block_end: int
    row_counts: dict[str, int]
    checksums: dict[str, str]
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.block_start > self.block_end:
            raise ValueError("block_start must not exceed block_end")
        if self.status == "success" and sum(self.row_counts.values()) <= 0:
            raise ValueError("success manifest must contain at least one row")
        if self.status == "success" and self.errors:
            raise ValueError("success manifest must not contain errors")
