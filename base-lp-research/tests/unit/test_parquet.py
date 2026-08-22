import unittest
from pathlib import Path

from base_lp.data.persist import read_parquet_rows, write_parquet


class ParquetTests(unittest.TestCase):
    def test_round_trips_canonical_rows(self):
        rows = [
            {"block_number": 1, "event_type": "swap", "amount0": -10},
            {"block_number": 2, "event_type": "swap", "amount0": 20},
        ]
        path = Path(__file__).resolve().parents[2] / ".test-tmp" / "events.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_parquet(path, rows)
            self.assertEqual(read_parquet_rows(path), rows)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
