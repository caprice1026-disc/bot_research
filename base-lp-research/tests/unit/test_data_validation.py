import unittest

from base_lp.data.validate import validate_log_order
from base_lp.schemas import LogRecord


def make_log(block_number: int, transaction_index: int, log_index: int) -> LogRecord:
    return LogRecord(
        block_number=block_number,
        transaction_index=transaction_index,
        log_index=log_index,
        block_hash=f"0x{block_number:x}",
        transaction_hash=f"0xtx{block_number:x}",
        address="0xpool",
        topics=["0xtopic"],
        data="0x",
        timestamp=block_number,
    )


class DataValidationTests(unittest.TestCase):
    def test_log_order_is_strict_and_duplicate_free(self):
        logs = [make_log(10, 0, 0), make_log(10, 0, 1), make_log(10, 1, 0)]

        report = validate_log_order(logs)

        self.assertTrue(report.valid)
        self.assertEqual(report.duplicate_keys, [])

    def test_duplicate_stable_key_is_invalid(self):
        logs = [make_log(10, 0, 0), make_log(10, 0, 0)]

        report = validate_log_order(logs)

        self.assertFalse(report.valid)
        self.assertEqual(report.duplicate_keys, [(10, 0, 0)])


if __name__ == "__main__":
    unittest.main()
