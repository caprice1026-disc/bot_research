import unittest
from decimal import Decimal as D
from paper_scan import Quote, economics, scan


def row(time="2026-10-20T00:00:00Z", amount="10", out="1502", **kwargs):
    return dict(timestamp=time, block_number=1, pool_id="verified_pool",
                amount_in_usdc=amount, amount_out_jpyc=out,
                fx_jpy_per_usdc="150", gas_cost_jpy="1",
                source_kind="historical_pool_quote", source="synthetic_unit_test_only",
                **kwargs)


class Tests(unittest.TestCase):
    def test_net_no_double_count(self):
        self.assertEqual(economics(Quote.parse(row()))["net_reference_profit_jpy"], D(1))

    def test_breakeven_skips(self):
        self.assertFalse(economics(Quote.parse(row(out="1501")))["profitable"])

    def test_gas_sensitivity(self):
        self.assertEqual(economics(Quote.parse(row()), gas_multiplier=D(0))["net_reference_profit_jpy"], D(2))

    def test_execution_haircut(self):
        self.assertFalse(economics(Quote.parse(row()), D(100))["profitable"])

    def test_reject_aggregate(self):
        r = row()
        r["source_kind"] = "aggregate_price"
        with self.assertRaises(ValueError):
            Quote.parse(r)

    def test_one_per_episode(self):
        rows = [row(), row("2026-10-20T00:05:00Z")]
        result, s = scan(rows, capital_usdc=D(100))
        self.assertEqual(s["conditional_selected_candidates"], 1)
        self.assertIsNone(s["cumulative_strategy_profit_jpy"])
        self.assertEqual(sum(r["conditional_selected_candidate"] for r in result), 1)

    def test_best_current_grid_not_future_hindsight(self):
        result, _ = scan([row(amount="20", out="3010"), row()], capital_usdc=D(100))
        chosen = [r for r in result if r["conditional_selected_candidate"]]
        self.assertEqual(chosen[0]["amount_in_usdc"], "20")

    def test_affordable_smaller_size(self):
        result, _ = scan([row(amount="20", out="3010"), row()], capital_usdc=D(15))
        chosen = [r for r in result if r["conditional_selected_candidate"]]
        self.assertEqual(chosen[0]["amount_in_usdc"], "10")

    def test_no_capital_recycling(self):
        rows = [row(), row("2026-10-20T00:05:00Z", out="1490"), row("2026-10-20T00:10:00Z")]
        _, s = scan(rows, capital_usdc=D(15))
        self.assertEqual(s["candidate_episodes"], 2)
        self.assertEqual(s["conditional_selected_candidates"], 1)

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            scan([row(), row()], capital_usdc=D(100))

    def test_empty_is_missing_not_zero_profit(self):
        _, summary = scan([], capital_usdc=D(100))
        self.assertEqual(summary["status"], "insufficient_historical_quotes")
        self.assertIsNone(summary["positive_snapshots"])
        self.assertIsNone(summary["realized_profit_jpy"])

    def test_nonfinite_capital_rejected(self):
        with self.assertRaises(ValueError):
            scan([], capital_usdc=D("NaN"))

    def test_multiple_pools_rejected(self):
        other = row("2026-10-20T00:05:00Z")
        other["pool_id"] = "other_pool"
        with self.assertRaises(ValueError):
            scan([row(), other], capital_usdc=D(100))

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):
            Quote.parse(row(out="NaN"))


if __name__ == "__main__":
    unittest.main()
