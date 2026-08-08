import unittest

from base_lp.data.normalize import log_record_from_rpc


class NormalizeTests(unittest.TestCase):
    def test_rpc_log_becomes_canonical_log_record(self):
        record = log_record_from_rpc(
            {
                "blockNumber": "0xa",
                "transactionIndex": "0x2",
                "logIndex": "0x7",
                "blockHash": "0xblock",
                "transactionHash": "0xtx",
                "address": "0x" + "ab" * 20,
                "topics": ["0xtopic"],
                "data": "0x00",
            },
            timestamp=123,
        )

        self.assertEqual(record.stable_key, (10, 2, 7))
        self.assertEqual(record.timestamp, 123)
        self.assertEqual(record.address, "0x" + "ab" * 20)


if __name__ == "__main__":
    unittest.main()
