from __future__ import annotations

from eth_hash.auto import keccak

from base_lp.schemas import LiquidityEvent, LogRecord, SwapEvent


SWAP_SIGNATURE = "Swap(address,address,int256,int256,uint160,uint128,int24)"
SWAP_TOPIC = "0x" + keccak(SWAP_SIGNATURE.encode("ascii")).hex()
MINT_TOPIC = "0x" + keccak(b"Mint(address,address,int24,int24,uint128,uint256,uint256)").hex()
BURN_TOPIC = "0x" + keccak(b"Burn(address,int24,int24,uint128,uint256,uint256)").hex()
COLLECT_TOPIC = "0x" + keccak(b"Collect(address,address,int24,int24,uint128,uint128)").hex()


def _words(data: str, expected: int) -> list[bytes]:
    raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
    if len(raw) != expected * 32:
        raise ValueError(f"expected {expected} ABI words, got {len(raw) // 32}")
    return [raw[offset : offset + 32] for offset in range(0, len(raw), 32)]


def _uint256(word: bytes) -> int:
    return int.from_bytes(word, "big", signed=False)


def _int256(word: bytes) -> int:
    value = _uint256(word)
    if value >= 1 << 255:
        value -= 1 << 256
    return value


def _address(topic: str) -> str:
    raw = bytes.fromhex(topic[2:] if topic.startswith("0x") else topic)
    if len(raw) != 32:
        raise ValueError("indexed address topic must be 32 bytes")
    return "0x" + raw[-20:].hex()


def decode_swap_log(log: LogRecord) -> SwapEvent:
    if not log.topics or log.topics[0].lower() != SWAP_TOPIC.lower():
        raise ValueError("log topic is not Uniswap v3 Swap")
    if len(log.topics) != 3:
        raise ValueError("Swap must contain two indexed address topics")
    amount0, amount1, sqrt_price_x96, liquidity, tick = _words(log.data, 5)
    return SwapEvent(
        block_number=log.block_number,
        transaction_index=log.transaction_index,
        log_index=log.log_index,
        block_hash=log.block_hash,
        transaction_hash=log.transaction_hash,
        address=log.address,
        timestamp=log.timestamp,
        sender=_address(log.topics[1]),
        recipient=_address(log.topics[2]),
        amount0=_int256(amount0),
        amount1=_int256(amount1),
        sqrt_price_x96=_uint256(sqrt_price_x96),
        liquidity=_uint256(liquidity),
        tick=_int256(tick),
    )


def decode_liquidity_log(log: LogRecord) -> LiquidityEvent:
    topic = log.topics[0].lower() if log.topics else ""
    if topic == BURN_TOPIC.lower():
        if len(log.topics) != 4:
            raise ValueError("Burn must contain owner and tick topics")
        amount, amount0, amount1 = _words(log.data, 3)
        event_type = "burn"
        owner = _address(log.topics[1])
        tick_lower = _int256(bytes.fromhex(log.topics[2][2:]))
        tick_upper = _int256(bytes.fromhex(log.topics[3][2:]))
    elif topic == MINT_TOPIC.lower():
        if len(log.topics) != 4:
            raise ValueError("Mint must contain owner and tick topics")
        sender, amount, amount0, amount1 = _words(log.data, 4)
        del sender
        event_type = "mint"
        owner = _address(log.topics[1])
        tick_lower = _int256(bytes.fromhex(log.topics[2][2:]))
        tick_upper = _int256(bytes.fromhex(log.topics[3][2:]))
    elif topic == COLLECT_TOPIC.lower():
        if len(log.topics) != 4:
            raise ValueError("Collect must contain owner and tick topics")
        recipient, amount0, amount1 = _words(log.data, 3)
        del recipient
        event_type = "collect"
        owner = _address(log.topics[1])
        tick_lower = _int256(bytes.fromhex(log.topics[2][2:]))
        tick_upper = _int256(bytes.fromhex(log.topics[3][2:]))
        amount = 0
    else:
        raise ValueError("log topic is not a supported liquidity event")
    return LiquidityEvent(
        event_type=event_type,
        block_number=log.block_number,
        transaction_index=log.transaction_index,
        log_index=log.log_index,
        block_hash=log.block_hash,
        transaction_hash=log.transaction_hash,
        address=log.address,
        timestamp=log.timestamp,
        owner=owner,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
        amount=_uint256(amount) if isinstance(amount, bytes) else amount,
        amount0=_uint256(amount0),
        amount1=_uint256(amount1),
    )
