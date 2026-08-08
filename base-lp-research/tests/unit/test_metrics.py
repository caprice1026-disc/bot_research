import unittest

from base_lp.backtest.models import BacktestResult, EquityPoint
from base_lp.metrics.performance import summarize_result


class MetricsTests(unittest.TestCase):
    def test_summary_reports_drawdown_and_time_in_range(self):
        result = BacktestResult(
            strategy_name="fixture",
            equity_curve=[
                EquityPoint(1, 1, 100.0, 1.0, 0.0, True),
                EquityPoint(2, 2, 110.0, 2.0, 0.0, False),
                EquityPoint(3, 3, 90.0, 2.0, 1.0, True),
            ],
            action_log=[{"action": "hold"}, {"action": "reset"}],
            total_fees_usd=2.0,
            total_costs_usd=1.0,
            terminal_value_usd=90.0,
        )

        summary = summarize_result(result, hodl_terminal_value_usd=95.0)

        self.assertEqual(summary["max_drawdown_usd"], 20.0)
        self.assertAlmostEqual(summary["time_in_range"], 2 / 3)
        self.assertEqual(summary["hodl_alpha_usd"], -5.0)
        self.assertEqual(summary["rebalances"], 1)


if __name__ == "__main__":
    unittest.main()
