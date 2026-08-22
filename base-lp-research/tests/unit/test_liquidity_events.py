import unittest

from eth_hash.auto import keccak

from base_lp.data.events import decode_liquidity_log
from base_lp.schemas import LogRecord


def topic(signature: str) -> str:
    return "0x" + keccak(signature.encode("ascii")).hex()


def indexed_int(value: int) -> str:
    return ((1 << 256) + value if value < 0 else value).to_bytes(32, "big").hex()


class LiquidityEventTests(unittest.TestCase):
    def test_decodes_burn_owner_ticks_and_amounts(self):
        owner = "0x" + "11" * 20
        log = LogRecord(
            block_number=1,
            transaction_index=2,
            log_index=3,
            block_hash="0xblock",
            transaction_hash="0xtx",
            address="0x" + "aa" * 20,
            topics=[
                topic("Burn(address,int24,int24,uint128,uint256,uint256)"),
                "0x" + "00" * 12 + owner[2:],
                "0x" + indexed_int(-100),
                "0x" + indexed_int(100),
            ],
            data="0x" + "".join(
                [
                    indexed_int(1234),
                    indexed_int(5000),
                    indexed_int(6000),
                ]
            ),
            timestamp=100,
        )

        event = decode_liquidity_log(log)

        self.assertEqual(event.event_type, "burn")
        self.assertEqual(event.owner, owner)
        self.assertEqual(event.tick_lower, -100)
        self.assertEqual(event.tick_upper, 100)
        self.assertEqual(event.amount, 1234)
        self.assertEqual(event.amount0, 5000)
        self.assertEqual(event.amount1, 6000)

    def test_decodes_collect_recipient_from_data_not_topics(self):
        owner = "0x" + "11" * 20
        recipient = "0x" + "22" * 20
        log = LogRecord(
            block_number=1,
            transaction_index=2,
            log_index=4,
            block_hash="0xblock",
            transaction_hash="0xtx",
            address="0x" + "aa" * 20,
            topics=[
                topic("Collect(address,address,int24,int24,uint128,uint128)"),
                "0x" + "00" * 12 + owner[2:],
                "0x" + indexed_int(-100),
                "0x" + indexed_int(100),
            ],
            data="0x" + "".join(
                [
                    "00" * 12 + recipient[2:],
                    indexed_int(100),
                    indexed_int(200),
                ]
            ),
            timestamp=100,
        )

        event = decode_liquidity_log(log)

        self.assertEqual(event.event_type, "collect")
        self.assertEqual(event.tick_lower, -100)
        self.assertEqual(event.tick_upper, 100)
        self.assertEqual(event.amount0, 100)
        self.assertEqual(event.amount1, 200)


if __name__ == "__main__":
    unittest.main()
