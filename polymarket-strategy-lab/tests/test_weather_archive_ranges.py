"""Compare bounded archive extraction against independently selected rows."""
from datetime import datetime, timezone
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".probe-deps"))
sys.path.insert(0, str(ROOT))
import pyarrow as pa
import pyarrow.parquet as pq
from weather_archive_ranges import extract


class CoalescedArchiveTests(unittest.TestCase):
    def test_subset_excludes_other_markets_types_and_end_boundary(self):
        chosen, other = bytes.fromhex("aa" * 32), bytes.fromhex("bb" * 32)
        rows = []
        for market, at, kind in ((chosen, 99, "book"), (chosen, 100, "book"), (chosen, 101, "last_trade_price"),
                                 (chosen, 109, "price_change"), (chosen, 110, "book"), (other, 105, "book")):
            rows.append({"market": market, "event_type": kind, "timestamp_received": datetime.fromtimestamp(at, timezone.utc),
                         "timestamp": datetime.fromtimestamp(at, timezone.utc), "asset_id": b"1", "best_bid": .3, "best_ask": .4,
                         "bids": [{"price": .3, "size": 10.}], "asks": [{"price": .4, "size": 20.}],
                         "new_tick_size": .01, "sequence": len(rows), "price": .4, "size": 20., "side": "BUY"})
        sink = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist(rows), sink, row_group_size=2)
        raw = sink.getvalue().to_pybytes()

        class LocalRanges(io.BytesIO):
            def __init__(self, url, cache):
                super().__init__(raw)
                self.length, self.segments = len(raw), []
            def fetch(self, start, size):
                return raw[start:start + size]

        with tempfile.TemporaryDirectory() as directory, patch("weather_archive_ranges.RangeFile", LocalRanges):
            output = Path(directory) / "subset.parquet"
            info = extract("https://dl.pendulumflow.com/v3/test", ["0x" + chosen.hex()], 100, 110, output, directory)
            result = pq.read_table(output).to_pylist()
            self.assertEqual(info["rows"], 2)
            self.assertEqual([r["sequence"] for r in result], [1, 3])
            self.assertTrue(all(r["market"] == chosen for r in result))


if __name__ == "__main__":
    unittest.main()
