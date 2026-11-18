"""Hand-calculated IOC scenarios and an actual production Book adapter."""
from collections import Counter
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT/"polymarket-strategy-lab")]
from l2_ioc_reference import SingleUseL2
from btc_data import Book
from btc_backtest import execute_leg

def depth():
    return SingleUseL2(((D(".42"),D("4")), (D(".43"),D("3"))),
                       ((D(".45"),D("5")), (D(".44"),D("2"))), 10)

class IOCReferenceTests(unittest.TestCase):
    def test_buy_walks_asks_best_first(self):
        r = depth().ioc(side="buy", quantity=6)
        self.assertEqual([(f.price,f.quantity) for f in r.fills], [(D(".44"),D("2")),(D(".45"),D("4"))])
        self.assertEqual((r.filled,r.canceled,r.notional,r.average_price),
                         (D("6"),D("0"),D("2.68"),D("2.68")/6))

    def test_missing_depth_cancels_remainder(self):
        r = depth().ioc(side="buy", quantity=10)
        self.assertEqual((r.filled,r.canceled,r.notional), (D("7"),D("3"),D("3.13")))

    def test_buy_limit_includes_equal_price_not_worse(self):
        r = depth().ioc(side="buy", quantity=6, limit=".44")
        self.assertEqual((r.filled,r.canceled,r.notional,r.average_price),
                         (D("2"),D("4"),D(".88"),D(".44")))

    def test_sell_walks_bids_best_first(self):
        r = depth().ioc(side="sell", quantity=5)
        self.assertEqual([(f.price,f.quantity) for f in r.fills], [(D(".43"),D("3")),(D(".42"),D("2"))])
        self.assertEqual((r.notional,r.average_price), (D("2.13"),D(".426")))

    def test_sell_limit_and_unmarketable_order(self):
        r = depth().ioc(side="sell", quantity=5, limit=".43")
        self.assertEqual((r.filled,r.canceled,r.notional), (D("3"),D("2"),D("1.29")))
        r = depth().ioc(side="buy", quantity=5, limit=".43")
        self.assertEqual((r.filled,r.canceled,r.notional,r.average_price),(D("0"),D("5"),D("0"),None))

    def test_single_use_even_after_zero_fill(self):
        book = depth()
        book.ioc(side="buy", quantity=1, limit=".40")
        with self.assertRaisesRegex(ValueError,"already consumed"):
            book.ioc(side="sell", quantity=1)

    def test_invalid_inputs_rejected(self):
        for side, qty, limit in (("maker",1,None),("buy",0,None),("buy","NaN",None),("sell",1,"Infinity")):
            with self.assertRaises(ValueError):
                depth().ioc(side=side,quantity=qty,limit=limit)
        for bids, asks in ((((.5,1),),((.4,1),)), (((.4,-1),),((.5,1),)),
                            (((.4,1),(.4,2)),((.5,1),))):
            with self.assertRaises(ValueError):
                SingleUseL2(bids,asks,10)

    def test_existing_book_adapter_and_top_only_difference(self):
        book = Book()
        book.update(dict(event_type="book",recv_ts=9.9,connection_id="a",source_event_ts=9900000,
            bid=.43,ask=.44,raw_payload=json.dumps({"bids":[{"price":".43","size":"10"}],
            "asks":[{"price":".44","size":"5"},{"price":".45","size":"100"}]})),Counter())
        before = dict(book.asks)
        config = json.loads((ROOT/"polymarket-strategy-lab/btc_config.json").read_text())
        row = dict(decision_ts=10,up_token_id="up",**book.features("up",10))
        self.assertIsNone(execute_leg(row,"up",.45,6,config))
        r = SingleUseL2.from_book(book,as_of=10,max_quote_age=1).ioc(side="buy",quantity=6,limit=".45")
        self.assertEqual((r.filled,r.notional,r.average_price),(D("6"),D("2.65"),D("2.65")/6))
        self.assertEqual(book.asks,before)
        for as_of in (9,12):
            with self.assertRaises(ValueError):
                SingleUseL2.from_book(book,as_of=as_of,max_quote_age=1)
        book.valid = False
        with self.assertRaises(ValueError):
            SingleUseL2.from_book(book,as_of=10,max_quote_age=1)

if __name__ == "__main__":
    unittest.main()
