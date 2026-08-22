from __future__ import annotations

from eth_hash.auto import keccak

from base_lp.data.rpc import normalize_address
from base_lp.schemas import PoolMetadata


ZERO_ADDRESS = "0x" + "00" * 20


def _selector(signature: str) -> str:
    return keccak(signature.encode("ascii"))[:4].hex()


def _word(value: int) -> str:
    return value.to_bytes(32, "big").hex()


def _address_word(address: str) -> str:
    return "00" * 12 + normalize_address(address)[2:]


def _decode_uint(result: str) -> int:
    return int(result[2:] if result.startswith("0x") else result, 16)


def _decode_address(result: str) -> str:
    raw = result[2:] if result.startswith("0x") else result
    if len(raw) < 40:
        raise ValueError("eth_call address result is too short")
    return normalize_address("0x" + raw[-40:])


def _call(rpc, to: str, signature: str, encoded_args: str = "") -> str:
    data = "0x" + _selector(signature) + encoded_args
    return str(rpc.request("eth_call", [{"to": normalize_address(to), "data": data}, "latest"]))


def resolve_pool(rpc, factory: str, token_a: str, token_b: str, fee_tier: int, chain_id: int) -> PoolMetadata:
    token_a = normalize_address(token_a)
    token_b = normalize_address(token_b)
    factory = normalize_address(factory)
    pool = _decode_address(
        _call(
            rpc,
            factory,
            "getPool(address,address,uint24)",
            _address_word(token_a) + _address_word(token_b) + _word(fee_tier),
        )
    )
    if pool == ZERO_ADDRESS:
        raise ValueError("factory returned zero pool address")
    token0 = _decode_address(_call(rpc, pool, "token0()"))
    token1 = _decode_address(_call(rpc, pool, "token1()"))
    fee = _decode_uint(_call(rpc, pool, "fee()"))
    tick_spacing = _decode_uint(_call(rpc, pool, "tickSpacing()"))
    if fee != fee_tier:
        raise ValueError(f"pool fee mismatch: expected {fee_tier}, got {fee}")
    token0_decimals = _decode_uint(_call(rpc, token0, "decimals()"))
    token1_decimals = _decode_uint(_call(rpc, token1, "decimals()"))
    return PoolMetadata(
        chain_id=chain_id,
        pool_address=pool,
        token0=token0,
        token1=token1,
        token0_decimals=token0_decimals,
        token1_decimals=token1_decimals,
        fee_tier=fee,
        tick_spacing=tick_spacing,
        creation_block=0,
    )
