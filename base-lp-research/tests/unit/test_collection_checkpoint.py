import unittest

from base_lp.cli import _checkpoint_matches, _new_checkpoint


class CollectionCheckpointTests(unittest.TestCase):
    def test_checkpoint_matches_the_same_collection_signature(self):
        checkpoint = _new_checkpoint(8453, "0x" + "ab" * 20, 10, 20, 5)

        self.assertTrue(_checkpoint_matches(checkpoint, 8453, "0x" + "ab" * 20, 10, 20, 5))
        self.assertFalse(_checkpoint_matches(checkpoint, 8453, "0x" + "ab" * 20, 10, 21, 5))


if __name__ == "__main__":
    unittest.main()
