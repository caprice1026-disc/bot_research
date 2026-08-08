import unittest
from pathlib import Path

from base_lp.data.persist import append_jsonl, write_jsonl, sha256_file


class PersistenceTests(unittest.TestCase):
    def test_jsonl_is_deterministic_appendable_and_checksumable(self):
        rows = [{"b": 2, "a": 1}, {"b": 3, "a": 4}]

        temp_root = Path(__file__).resolve().parents[2] / ".test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        path = temp_root / "events.jsonl"
        try:
            write_jsonl(path, rows)
            first = path.read_bytes()
            digest = sha256_file(path)
            append_jsonl(path, [{"b": 4, "a": 5}])

            self.assertTrue(path.read_bytes().startswith(first))
            self.assertEqual(len(digest), 64)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
