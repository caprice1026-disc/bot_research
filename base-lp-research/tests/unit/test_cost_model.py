import unittest

from base_lp.backtest.cost_model import BlockFee, CostModel


class CostModelTests(unittest.TestCase):
    def test_cost_breakdown_contains_l2_l1_and_swap_friction(self):
        model = CostModel(fee_tier=500, include_inventory_swap_fee=True)

        cost = model.estimate(
            action="reset",
            block_fee=BlockFee(gas_price_wei=1_000_000, gas_used=200_000, l1_fee_wei=300_000_000_000_000),
            token_price_usd=2_000.0,
            inventory_swap_usd=1_000.0,
        )

        self.assertGreater(cost.l2_execution_usd, 0)
        self.assertGreater(cost.l1_security_usd, 0)
        self.assertGreater(cost.inventory_swap_fee_usd, 0)
        self.assertEqual(cost.total_usd, sum(cost.components()))


if __name__ == "__main__":
    unittest.main()
