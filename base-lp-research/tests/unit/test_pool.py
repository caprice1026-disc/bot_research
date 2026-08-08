import unittest

from eth_hash.auto import keccak

from base_lp.data.pool import resolve_pool


def call_data(signature: str) -> str:
    return "0x" + keccak(signature.encode("ascii"))[:4].hex()


def padded_address(address: str) -> str:
    return "0x" + "00" * 12 + address[2:]


def padded_uint(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


class FakeRpc:
    def __init__(self, pool: str, token0: str, token1: str):
        self.pool = pool
        self.token0 = token0
        self.token1 = token1

    def request(self, method, params):
        if method != "eth_call":
            raise AssertionError(method)
        data = params[0]["data"]
        selector = data[:10]
        if selector == call_data("getPool(address,address,uint24)"):
            return padded_address(self.pool)
        if selector == call_data("token0()"):
            return padded_address(self.token0)
        if selector == call_data("token1()"):
            return padded_address(self.token1)
        if selector == call_data("fee()"):
            return padded_uint(500)
        if selector == call_data("tickSpacing()"):
            return padded_uint(10)
        if selector == call_data("decimals()"):
            return padded_uint(6 if len(self._decimals_calls()) == 1 else 18)
        raise AssertionError(selector)

    def _decimals_calls(self):
        return []


class PoolResolutionTests(unittest.TestCase):
    def test_factory_resolution_reads_pool_immutables(self):
        token0 = "0x" + "11" * 20
        token1 = "0x" + "22" * 20
        pool = "0x" + "33" * 20

        metadata = resolve_pool(
            FakeRpc(pool, token0, token1),
            factory="0x" + "44" * 20,
            token_a=token0,
            token_b=token1,
            fee_tier=500,
            chain_id=8453,
        )

        self.assertEqual(metadata.pool_address, pool)
        self.assertEqual(metadata.fee_tier, 500)
        self.assertEqual(metadata.tick_spacing, 10)
        self.assertEqual(metadata.token0, token0)
        self.assertEqual(metadata.token1, token1)


if __name__ == "__main__":
    unittest.main()
