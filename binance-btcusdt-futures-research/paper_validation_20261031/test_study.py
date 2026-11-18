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
    def _gap(self, side, mark, rate=0., fee_bps=0., initial=10000., trade=None, extreme=None):
        """Three priced minutes: open, scheduled flat, attempted re-entry."""
        m=constant_market(3)
        for key in ("open","high","low","close"):
            m[key][1:] = mark if trade is None else trade
        for key in ("mark_open","mark_high","mark_low"):
            m[key][1:] = mark
        if extreme is not None:
            m["mark_low" if side > 0 else "mark_high"][1] = extreme
        m["funding_rate"][1]=rate
        cfg={**CFG,"fee_bps":fee_bps,"initial_equity":initial}
        return replay(m,[0,1,2],[side*2.,0.,side],0,3,cfg,0.)

    def test_opening_gap_liquidates_old_long_and_short_before_flat_and_halts(self):
        for side,mark in [(1,40.),(-1,160.)]:
            with self.subTest(side=side):
                eq,exp,q,t=self._gap(side,mark)
                np.testing.assert_allclose(eq,[10000.,0.,0.])
                np.testing.assert_allclose(q,[side*200.,0.,0.])
                self.assertEqual(t[5],-12000.)   # 200 * adverse 60
                self.assertEqual(t[6],2)         # open + forced exit, not a later re-entry
                self.assertEqual(t[7],1)         # a minute counted at most once
                self.assertEqual(t[8],1)
                self.assertEqual(t[9],2000.)     # proxy floor, not investment PnL
                self.assertAlmostEqual(eq[-1],10000.+t[5]-t[1]-t[2]-t[3]+t[9])

    def test_gap_uses_mark_open_not_trade_open_or_future_minute_extreme(self):
        # 200 * (50.25 - 100) = -9,950. Equity 50 <= maintenance 50.25.
        # Trade open is 95; the minute's later worst mark is 1. Neither prices the forced exit.
        eq,_,q,t=self._gap(1,50.25,trade=95.,extreme=1.)
        np.testing.assert_allclose(eq,[10000.,50.,50.])
        self.assertEqual(t[8],1)
        self.assertEqual(t[9],0.)
        self.assertEqual(q[-1],0.)

    def test_boundary_funding_can_trigger_or_avert_breach_before_flat(self):
        # Old q=200, mark 50.5: equity 100, maintenance 50.5.
        # Paying 50.5 (rate 0.005) leaves 49.5 and triggers liquidation.
        eq,_,_,t=self._gap(1,50.5,rate=.005)
        self.assertAlmostEqual(t[3],50.5)
        self.assertAlmostEqual(eq[1],49.5)
        self.assertEqual(t[8],1)
        # Old equity 50 at mark 50.25; receiving 50.25 averts opening breach.
        eq,_,_,t=self._gap(1,50.25,rate=-.005,extreme=1.)
        self.assertAlmostEqual(t[3],-50.25)
        self.assertAlmostEqual(eq[1],100.25)
        self.assertEqual(t[8],0)  # elective flat before any later extreme

    def test_gap_floor_reconciles_actual_forced_close_fees(self):
        # 10,020 starting cash, 200 @ 100, 20 entry fee. At 40: -2,000
        # before 8 forced-close fee; the model absorbs the resulting 2,008.
        eq,_,_,t=self._gap(1,40.,fee_bps=10.,initial=10020.)
        self.assertAlmostEqual(t[1],28.)
        self.assertAlmostEqual(t[4],28000.)
        self.assertAlmostEqual(t[9],2008.)
        self.assertAlmostEqual(eq[-1],10020.+t[5]-t[1]-t[2]-t[3]+t[9])

    def test_solvent_flat_is_not_liquidated_by_future_intrabar_low(self):
        eq,_,_,t=self._gap(1,60.,extreme=1.)
        self.assertEqual(eq[1],2000.)
        self.assertEqual(t[8],0)
        self.assertEqual(t[9],0.)

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
