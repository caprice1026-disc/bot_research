import unittest

from base_lp.data.ingest import timestamp_to_block


class FakeRpc:
    def block_by_number(self, block_number: int, full_transactions: bool = False):
        if block_number > 10:
            return None
        return {"timestamp": hex(block_number * 10)}


class BlockTimeTests(unittest.TestCase):
    def test_binary_search_finds_first_block_at_or_after_timestamp(self):
        self.assertEqual(timestamp_to_block(FakeRpc(), 55, latest_block=10), 6)


if __name__ == "__main__":
    unittest.main()
