import unittest,json,csv
from decimal import Decimal as D
from run_redemption import simulate_redemption,OUT,BASE
class RedemptionTests(unittest.TestCase):
    def sim(self,c=100,p='100',delay=0,reinvest=True,g='base'):
        return simulate_redemption(c,p,g,delay,reinvest,False)
    def test_invalid_input_rejected(self):
        for capital,delay in ((0,0),(-1,0),(100,-1)):
            with self.assertRaises(ValueError):self.sim(capital,delay=delay)
    def test_no_affordable_trade_zero_profit(self):
        r=self.sim(100,'1000');self.assertEqual(r['trade_count'],0);self.assertEqual(D(r['net_profit_assuming_all_one_yen_redemptions_jpy']),0)
    def test_negative_estimated_profit_skipped(self):
        r=self.sim(100,'1',g='high');self.assertEqual(r['trade_count'],0)
    def test_one_lot_no_reinvest(self):
        r=self.sim(reinvest=False);self.assertEqual(r['trade_count'],1);self.assertEqual(r['settled_lot_count'],1);self.assertAlmostEqual(float(r['settled_lot_net_profit_jpy']),50.244301040552116)
    def test_delay24_not_realized_within_window(self):
        r=self.sim(delay=86400);self.assertEqual(r['trade_count'],1);self.assertEqual(r['settled_lot_count'],0);self.assertEqual(D(r['settled_lot_net_profit_jpy']),0);self.assertGreater(D(r['pending_redemption_gross_jpy']),0)
    def test_same_lots_delay_changes_realization_not_nav(self):
        a=self.sim(delay=0,reinvest=False);b=self.sim(delay=86400)
        self.assertEqual(D(a['net_profit_assuming_all_one_yen_redemptions_jpy']),D(b['net_profit_assuming_all_one_yen_redemptions_jpy']))
        self.assertNotEqual(a['settled_lot_count'],b['settled_lot_count'])
    def test_delay0_max_one_order_per_observation(self):
        r=self.sim();self.assertEqual(r['trade_count'],285);self.assertEqual(r['trade_count'],r['observations'])
    def test_delayed_cash_lock_reduces_turnover(self):
        a=self.sim(delay=0);b=self.sim(delay=3600);c=self.sim(delay=86400)
        self.assertGreater(a['trade_count'],b['trade_count']);self.assertGreater(b['trade_count'],c['trade_count'])
    def test_accounting_reconciles_pending(self):
        r=self.sim(1000,'greedy_grid',3600)
        cash=D(r['ending_cash_before_external_gas_jpy']);init=D(r['initial_swap_capital_jpy']);rec=D(r['settled_receipts_jpy']);spent=D(r['total_acquisition_cost_jpy']);pending=D(r['pending_redemption_gross_jpy']);gas=D(r['total_external_gas_cost_jpy']);profit=D(r['net_profit_assuming_all_one_yen_redemptions_jpy'])
        self.assertLess(abs(cash-init-rec+spent),D('1e-40'))
        self.assertLess(abs(cash+pending-gas-init-profit),D('1e-40'))
        self.assertLess(abs(D(r['settled_lot_net_profit_jpy'])+D(r['pending_lot_reference_net_profit_jpy'])-profit),D('1e-40'))
    def test_settlement_before_same_time_order(self):
        simulate_redemption(100,'100','base',3600,True,True)
        with open(OUT/'capital_100_policy_100_reinvest_delay_1h_gas_base_trades.csv',encoding='utf-8-sig') as f:lots=list(csv.DictReader(f))
        self.assertEqual(lots[1]['entry_time_utc'],lots[0]['redemption_due_utc'])
    def test_full_initial_jpy_budget_restricts_no_reinvest(self):
        r=self.sim(1000,'100',reinvest=False);self.assertLessEqual(D(r['total_acquisition_cost_jpy']),D(r['initial_swap_capital_jpy']));self.assertEqual(r['trade_count'],9)
    def test_80_scenarios_and_no_actual_transactions(self):
        rows=json.loads((OUT/'summaries.json').read_text());self.assertEqual(len(rows),80)
        for r in rows:
            self.assertFalse(r['actual_transactions']);self.assertIsNone(r['actual_realized_profit']);self.assertGreaterEqual(D(r['ending_cash_before_external_gas_jpy']),0)
if __name__=='__main__':unittest.main(verbosity=2)
