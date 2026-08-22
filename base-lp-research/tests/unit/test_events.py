import unittest

from eth_hash.auto import keccak

from base_lp.data.events import decode_swap_log
from base_lp.schemas import LogRecord


def abi_word(value: int, signed: bool = False) -> str:
    if signed and value < 0:
        value = (1 << 256) + value
    return value.to_bytes(32, "big").hex()


class EventDecodeTests(unittest.TestCase):
    def test_decodes_swap_event_with_signed_amounts_and_tick(self):
        topic0 = "0x" + keccak(b"Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
        sender = "0x" + "11" * 20
        recipient = "0x" + "22" * 20
        topics = [
            topic0,
            "0x" + "00" * 12 + sender[2:],
            "0x" + "00" * 12 + recipient[2:],
        ]
        data = "0x" + "".join(
            [
                abi_word(-123, signed=True),
                abi_word(456, signed=True),
                abi_word(79228162514264337593543950336),
                abi_word(987654),
                abi_word(-120, signed=True),
            ]
        )
        log = LogRecord(
            block_number=10,
            transaction_index=2,
            log_index=7,
            block_hash="0xblock",
            transaction_hash="0xtx",
            address="0xpool",
            topics=topics,
            data=data,
            timestamp=100,
        )

        event = decode_swap_log(log)

        self.assertEqual(event.sender, sender)
        self.assertEqual(event.recipient, recipient)
        self.assertEqual(event.amount0, -123)
        self.assertEqual(event.amount1, 456)
        self.assertEqual(event.sqrt_price_x96, 79228162514264337593543950336)
        self.assertEqual(event.liquidity, 987654)
        self.assertEqual(event.tick, -120)

    def test_rejects_non_swap_topic(self):
        log = LogRecord(
            block_number=1,
            transaction_index=0,
            log_index=0,
            block_hash="0xblock",
            transaction_hash="0xtx",
            address="0xpool",
            topics=["0x" + "00" * 32],
            data="0x",
            timestamp=1,
        )

        with self.assertRaises(ValueError):
            decode_swap_log(log)


if __name__ == "__main__":
    unittest.main()
