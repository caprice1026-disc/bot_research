import unittest
from pathlib import Path

from base_lp.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_yaml_config_and_exposes_typed_chain_settings(self):
        config = load_config(Path(__file__).resolve().parents[2] / "configs" / "base_weth_usdc_005.yaml")

        self.assertEqual(config.chain_id, 8453)
        self.assertEqual(config.fee_tier, 500)
        self.assertEqual(config.token_pair, "WETH/USDC")
        self.assertEqual(config.counterfactual_mode, "C0")
        self.assertEqual(config.collection_chunk_size, 500)
        self.assertEqual(config.rpc_timeout_seconds, 20.0)
        self.assertEqual(config.max_runtime_seconds, 90.0)
        self.assertEqual(config.collection_source, "dune")
        self.assertEqual(config.dune_api_env, "DUNE_API_KEY")
        self.assertEqual(config.dune_poll_interval_seconds, 5.0)
        self.assertEqual(config.pilot_start_utc, "2026-06-01T00:00:00Z")
        self.assertEqual(config.pilot_end_utc, "2026-06-08T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
