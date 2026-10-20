import unittest
from lp_sim import *
from replay import apply
class TestLP(unittest.TestCase):
 def test_no_own_position_identical(self):
  a,b=initial(),newpool()
  for e in events():
   ra,rb=apply(a,e),apply(b,e)
   self.assertEqual((a.sqrt,a.tick,a.liquidity),(b.sqrt,b.tick,b.liquidity))
   if ra:self.assertEqual(ra,rb)
 def test_mint_burn_rounding(self):
  p=newpool();L=10**17;lo=p.tick//10*10-100;hi=lo+200
  old=p.liquidity;a,b=p.mint(lo,hi,L);x,y,f,g=p.burn()
  self.assertEqual(p.liquidity,old);self.assertEqual((f,g),(0,0))
  self.assertTrue(0<=a-x<=1 and 0<=b-y<=1)
 def test_single_segment_fee_and_state(self):
  p=newpool();lo=p.tick//10*10-100;hi=lo+200;own=p.liquidity//10;p.mint(lo,hi,own)
  active=p.liquidity;result=p.swap(1000,True)
  self.assertEqual(result['steps'],1)
  lpfee=result['fee_raw']-result['amount_in']*125//10**6
  self.assertEqual(p.values()[2],own*(lpfee*Q128//active)//Q128)
  self.assertLessEqual(p.values()[2],lpfee)
 def test_usdc_only_direction(self):
  p=newpool();lo=p.tick//10*10+10;hi=lo+100
  x,y=p.mint(lo,hi,10**16);self.assertGreater(x,0);self.assertEqual(y,0)
  p.sqrt=sqrt_at_tick(hi);p.tick=hi
  x,y,_,_=p.values();self.assertEqual(x,0);self.assertGreater(y,0)
 def test_discount_bound_and_finite_cash(self):
  r=one_sided(1000,'.005',delay=86400,exit_fraction='.25',recycle=True,write=False)
  self.assertLessEqual(D(r['band_jpyc_price_at_initial_fx_high']),D('.995'))
  self.assertEqual(r['mints'],1);self.assertGreater(D(r['pending_gross_jpy']),0)
  self.assertGreaterEqual(D(r['cash_after_receipts_before_external_gas_jpy']),0)
  nav=D(r['cash_after_receipts_before_external_gas_jpy'])+D(r['pending_gross_jpy'])-D(r['gas_jpy'])
  self.assertEqual(nav,D(r['ending_nav_one_yen_jpy']))
 def test_unfilled_has_no_alpha(self):
  r=one_sided(1000,'.01',recycle=True,write=False)
  self.assertEqual(D(r['fees_one_yen_jpy']),0)
  self.assertAlmostEqual(float(D(r['net_vs_usdc_hold_one_yen'])), -float(D(r['gas_jpy'])),places=3)
if __name__=='__main__':unittest.main(verbosity=2)
