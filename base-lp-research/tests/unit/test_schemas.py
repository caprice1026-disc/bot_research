import unittest

from base_lp.schemas import DatasetManifest, LogRecord, PoolMetadata


class SchemaTests(unittest.TestCase):
    def test_log_record_stable_key_orders_chain_location(self):
        log = LogRecord(
            block_number=10,
            transaction_index=2,
            log_index=7,
            block_hash="0xabc",
            transaction_hash="0xtx",
            address="0xpool",
            topics=["0xtopic"],
            data="0x",
            timestamp=100,
        )

        self.assertEqual(log.stable_key, (10, 2, 7))

    def test_manifest_rejects_success_without_rows(self):
        pool = PoolMetadata(
            chain_id=8453,
            pool_address="0xpool",
            token0="0xtoken0",
            token1="0xtoken1",
            token0_decimals=18,
            token1_decimals=6,
            fee_tier=500,
            tick_spacing=10,
            creation_block=1,
        )

        with self.assertRaises(ValueError):
            DatasetManifest(
                status="success",
                source="fixture",
                chain_id=8453,
                pool=pool,
                block_start=1,
                block_end=2,
                row_counts={"swaps": 0},
                checksums={},
            )


if __name__ == "__main__":
    unittest.main()
