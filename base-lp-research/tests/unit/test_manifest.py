import json
import unittest
from pathlib import Path

from base_lp.data.manifest import build_manifest, write_manifest
from base_lp.schemas import PoolMetadata


class ManifestTests(unittest.TestCase):
    def test_manifest_serializes_status_and_checksums(self):
        pool = PoolMetadata(8453, "0xpool", "0xtoken0", "0xtoken1", 18, 6, 500, 10, 0)
        manifest = build_manifest(
            status="success",
            source="fixture",
            pool=pool,
            block_start=1,
            block_end=2,
            row_counts={"swaps": 1},
            checksums={"events.parquet": "abc"},
        )
        path = Path(__file__).resolve().parents[2] / ".test-tmp" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_manifest(path, manifest)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["pool"]["fee_tier"], 500)
            self.assertEqual(data["checksums"]["events.parquet"], "abc")
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
