import unittest,json
from decimal import Decimal as D
from v4_math import *
from replay import initial,events,apply,BASE
from run_stateful import validate_grid,simulate
class MathTests(unittest.TestCase):
    def test_tick_known_boundaries(self):
        self.assertEqual(sqrt_at_tick(0),Q96)
        self.assertEqual(sqrt_at_tick(-887272),4295128739)
        self.assertEqual(sqrt_at_tick(887272),1461446703485210103287273052203988822378723970342)
        for t in (-10000,-1,0,1,326990,327000):self.assertEqual(tick_at_sqrt(sqrt_at_tick(t)),t)
    def test_all_1140_historical_quotes_exact(self):
        r=validate_grid();self.assertEqual(r['observed_state_raw_exact_matches'],1140)
        self.assertLess(D(r['max_baseline_quote_error_jpy']),D('0.000001'))
    def test_no_trade_baseline_tick_liquidity(self):
        p=initial();count=0
        for e in events():
            apply(p,e)
            if e['type']=='swap':
                count+=1;self.assertEqual(p.tick,e['tick']);self.assertEqual(p.liquidity,e['liquidity'])
                self.assertLess(abs((D(p.sqrt)/e['sqrt'])**2-1),D('1e-12'))
        self.assertEqual(count,120)
    def test_own_impact_persists(self):
        p=initial();q1=p.swap(10*10**6);sqrt1=p.sqrt;q2=p.swap(10*10**6)
        self.assertLess(p.sqrt,sqrt1);self.assertLess(q2['amount_out'],q1['amount_out'])
    def test_split_costs_and_no_price_reset(self):
        whole=initial().swap(100*10**6)['amount_out'];p=initial();parts=sum(p.swap(10*10**6)['amount_out'] for _ in range(10))
        self.assertLessEqual(parts,whole)
    def test_unknown_bitmap_fails(self):
        p=initial();p.tick=325000;p.min_word=p.max_word=128
        with self.assertRaises(UnknownRange):p.swap(1000000)
    def test_exactout_roundtrip(self):
        p=initial();ain=10*10**6;out=p.clone().swap(ain)['amount_out'];rev=p.clone().swap(out,exact_in=False)
        self.assertEqual(rev['amount_out'],out);self.assertLessEqual(rev['amount_in'],ain)
    def test_lp_change_active_and_inactive(self):
        p=initial();L=p.liquidity;t=(p.tick//10)*10
        p.modify(t,t+10,123);self.assertEqual(p.liquidity,L+123)
        p.modify(t+20,t+30,50);self.assertEqual(p.liquidity,L+123)
        p.modify(t,t+10,-123);self.assertEqual(p.liquidity,L)
    def test_capital_not_recycled(self):
        r=simulate(100,'10','base');self.assertEqual(r['trade_count'],10);self.assertEqual(D(r['spent_usdc']),100);self.assertEqual(D(r['ending_cash_usdc']),0)
    def test_no_profit_no_trade(self):
        r=simulate(100,'1','high');self.assertEqual(r['trade_count'],0);self.assertEqual(D(r['ending_cash_usdc']),100)
    def test_realized_profit_not_claimed(self):
        r=simulate(100,'100','base');self.assertIsNone(r['realized_profit']);self.assertGreater(D(r['acquisition_reference_surplus_jpy']),0);self.assertLess(D(r['hypothetical_dex_exit_vs_hold_usdc_jpy']),0)
if __name__=='__main__':unittest.main(verbosity=2)
