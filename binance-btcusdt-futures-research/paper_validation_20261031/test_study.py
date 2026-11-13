"""Accounting, causal timing, statistical isolation and crash recovery checks."""
import unittest
import numpy as np
from engine import features,period_outcomes,replay,fit_policy
from operational_audit import ToyExchange,receipt_account,recover


def constant_market(n=10,price=100.):
    m={key:np.full(n,price) for key in ("open","high","low","close","mark_open","mark_high","mark_low")}
    m["timestamp"]=np.arange(n,dtype=np.int64)*60000
    m["funding_rate"]=np.zeros(n)
    return m


CFG=dict(initial_equity=10000.,fee_bps=5.,maintenance_margin_rate_proxy=.005,near_liquidation_margin_multiple=2.)


class AccountingTests(unittest.TestCase):
    def test_round_trip_cost_is_charged_on_actual_notional(self):
        m=constant_market()
        eq,_,_,totals=replay(m,[0],[1.],0,10,CFG,slip_bps=1.)
        traded=10000/1.0006
        self.assertAlmostEqual(eq[-1],10000-2*traded*.0006,places=8)
        self.assertAlmostEqual(totals[1],2*traded*.0005,places=8)
        self.assertAlmostEqual(totals[2],2*traded*.0001,places=8)

    def test_funding_before_boundary_order_and_sign(self):
        m=constant_market();m["funding_rate"][5]=.01
        config={**CFG,"fee_bps":0.}
        a=replay(m,[0,5],[1.,0.],0,10,config,0.)[3]
        b=replay(m,[0,5],[-1.,0.],0,10,config,0.)[3]
        c=replay(m,[5],[1.],0,10,config,0.)[3]
        self.assertAlmostEqual(a[3],100.)
        self.assertAlmostEqual(b[3],-100.)
        self.assertEqual(c[3],0.)

    def test_training_funding_matches_boundary_ownership(self):
        m=constant_market(12);m["funding_rate"][[1,6]]=[.01,.02]
        _,f,_=period_outcomes(m,np.array([1,6]))
        np.testing.assert_allclose(f,[.02,0.])

    def test_pnl_accounting_with_price_movement(self):
        m=constant_market();m["open"][5:]=110.;m["close"][5:]=110.
        m["mark_open"][5:]=110.;m["mark_high"][5:]=110.;m["mark_low"][5:]=110.
        m["funding_rate"][5]=.001
        eq,_,_,t=replay(m,[0,5],[1.,-.5],0,10,CFG,1.)
        self.assertAlmostEqual(eq[-1],10000+t[5]-t[1]-t[2]-t[3],places=8)

    def test_mark_liquidation_proxy_stops_new_risk(self):
        m=constant_market();m["mark_low"][2]=1.
        eq,_,q,t=replay(m,[0,5],[2.,2.],0,10,CFG,1.)
        self.assertEqual(t[8],1)
        self.assertEqual(q[-1],0.)
        self.assertGreaterEqual(eq[-1],0.)

    def test_no_trade_when_no_signal(self):
        m=constant_market()
        eq,_,_,t=replay(m,[0,5],[0.,0.],0,10,CFG,5.)
        self.assertEqual(eq[-1],10000.)
        self.assertEqual(t[6],0.)


class LeakageTests(unittest.TestCase):
    def test_future_bars_do_not_change_past_features(self):
        rng=np.random.default_rng(1)
        m=constant_market(4000)
        m["close"]=100*np.exp(np.cumsum(rng.normal(0,.001,4000)))
        a=features(m);m["close"][2500:]*=1.8;b=features(m)
        mask=a["decision_index"]<=2500
        for key in ("z15","z60","z240","vol"):
            np.testing.assert_array_equal(a[key][mask],b[key][mask])
        self.assertTrue(np.all(a["execution_index"]==a["decision_index"]+1))

    def test_future_returns_do_not_change_fitted_policy(self):
        rng=np.random.default_rng(2)
        states=rng.integers(18,size=5000);returns=rng.normal(0,.001,5000)
        funding=np.zeros(5000);train=np.arange(5000)<3000;actions=np.array([-1.,-.5,0.,.5,1.])
        a,_=fit_policy(states,returns,funding,train,actions,.0006,10.,.99)
        returns[3000:]=10.;funding[3000:]=1.
        b,_=fit_policy(states,returns,funding,train,actions,.0006,10.,.99)
        np.testing.assert_array_equal(a,b)


class RecoveryTests(unittest.TestCase):
    def test_partial_fill_cancels_remainder_and_protects_actual_quantity(self):
        ex=ToyExchange(100.);ex.submit("one",2.);ex.fill("one",.5)
        self.assertEqual(recover(ex)["status"],"reconciled")
        ex.fill("one")
        self.assertEqual(ex.quantity,1.)
        self.assertEqual(ex.protections["SL"]["quantity"],-1.)
        self.assertEqual(ex.protections["TP"]["quantity"],-1.)
        before=ex.snapshot();recover(ex);self.assertEqual(ex.snapshot(),before)

    def test_duplicate_ids_and_notifications(self):
        ex=ToyExchange(100.);ex.submit("one",2.);ex.fill("one")
        ex.submit("one",2.);ex.fill("one")
        self.assertEqual(ex.quantity,2.)
        self.assertEqual(receipt_account(ex.fills+ex.fills),receipt_account(ex.fills))

    def test_unavailable_and_unreconciled_exchange_halt(self):
        ex=ToyExchange(100.,quantity=1.)
        before=ex.snapshot()
        self.assertEqual(recover(ex,False)["status"],"halted_unavailable")
        self.assertEqual(recover(ex)["status"],"halted_ledger_mismatch")
        self.assertEqual(ex.snapshot(),before)


if __name__=="__main__":unittest.main(verbosity=2)
