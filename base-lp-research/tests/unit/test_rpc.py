import unittest

from base_lp.data.rpc import hex_to_int, normalize_address


class RpcHelpersTests(unittest.TestCase):
    def test_decodes_hex_quantities_and_normalizes_address(self):
        self.assertEqual(hex_to_int("0x2a"), 42)
        self.assertEqual(
            normalize_address("0xABCDEFabcdefABCDEFabcdefABCDEFabcdefABCD"),
            "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
        )

    def test_rejects_malformed_address(self):
        with self.assertRaises(ValueError):
            normalize_address("0x1234")


if __name__ == "__main__":
    unittest.main()
