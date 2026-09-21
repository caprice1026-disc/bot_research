from copy import deepcopy
from decimal import Decimal as D
import pytest
from position_stg.config import Config
from position_stg.domain import account
from position_stg.store import SQLiteStore
from position_stg.testnet import TestnetController as Controller, coverage


class Exchange:
    def __init__(self):
        self.quantity = D(0)
        self.orders = []
        self.sent = []
        self.responses = {}
        self.reject_stops = False
        self.after_fill = None

    def snapshot(self):
        return {"quantity": str(self.quantity), "entry": "100", "equity": "500", "mark": "100", "step": ".01", "orders": deepcopy(self.orders)}

    def lookup(self, key):
        return self.responses.get(key, {"status": "unknownOid"})

    def fills(self, since):
        return []

    def cancel(self, oid):
        self.orders = [o for o in self.orders if o["oid"] != oid]

    def order(self, delta, mark, client_id, *, stop=None, reduce_only=False):
        self.sent.append({"delta": delta, "stop": stop, "reduce_only": reduce_only})
        oid = len(self.sent)
        if stop is not None:
            if self.reject_stops:
                self.responses[client_id] = {"order": {"status": "rejected", "order": {"oid": oid}}}
                return {"status": "err"}
            self.orders.append({"coin": "BTC", "reduceOnly": True, "isTrigger": True, "orderType": "Stop Market", "side": "A" if self.quantity > 0 else "B", "triggerPx": str(stop), "sz": str(abs(delta)), "oid": oid})
            status = "open"
        else:
            if reduce_only:
                assert self.quantity * delta < 0
                delta = min(abs(delta), abs(self.quantity)).copy_sign(delta)
            self.quantity += delta
            status = "filled"
        self.responses[client_id] = {"order": {"status": status, "order": {"oid": oid}}}
        if stop is None and self.after_fill:
            hook, self.after_fill = self.after_fill, None
            hook()
        return {"status": "ok"}


def test_lost_open_response_does_not_leave_naked_position(tmp_path):
    exchange = Exchange()
    store = SQLiteStore(tmp_path / "testnet.db")
    controller = Controller(Config(), store, exchange, clock=lambda: 300)
    exchange.after_fill = lambda: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(TimeoutError):
        controller.tick({"intent": "set_target", "target_multiple": "1", "stop_price": "99", "reason": "test"}, 300)
    assert exchange.quantity == 0
    assert exchange.sent[-1]["reduce_only"]
    controller.tick({"intent": "hold", "target_multiple": None, "stop_price": None, "reason": "test"}, 300)
    assert exchange.quantity == 0


def test_db_failure_after_fill_uses_exchange_only_emergency(tmp_path):
    exchange = Exchange()
    store = SQLiteStore(tmp_path / "testnet.db")
    controller = Controller(Config(), store, exchange, clock=lambda: 300)
    def fail_db():
        store.write = lambda *a: (_ for _ in ()).throw(OSError("db unavailable"))
    exchange.after_fill = fail_db
    with pytest.raises(OSError):
        controller.tick({"intent": "set_target", "target_multiple": "1", "stop_price": "99", "reason": "test"}, 300)
    assert exchange.quantity == 0
    assert exchange.sent[-1]["reduce_only"]


def test_rejected_tighter_stop_records_actual_stop_and_reduces(tmp_path):
    exchange = Exchange()
    exchange.quantity = D(5)
    exchange.orders = [{"coin": "BTC", "reduceOnly": True, "isTrigger": True, "orderType": "Stop Market", "side": "A", "triggerPx": "90", "sz": "5", "oid": 77}]
    exchange.reject_stops = True
    store = SQLiteStore(tmp_path / "testnet.db")
    a = account(500)
    a.update(quantity="5", entry="100", stop="90")
    store.write("runtime", {"last_cycle": 0, "last_quantity": "5", "manual_pause": False, "account": a, "stop": "90", "owned_oids": [77], "pending_intents": [], "last_reconciled": 0}, 0)
    controller = Controller(Config(), store, exchange, clock=lambda: 300)
    controller.tick({"intent": "hold", "target_multiple": None, "stop_price": "99", "reason": "tighten"}, 300)
    assert store.read("runtime")[0]["stop"] == "90"
    assert exchange.quantity * 10 <= 15


def test_wrong_side_stop_is_not_protection():
    exchange = Exchange()
    exchange.quantity = D(1)
    exchange.orders = [{"coin": "BTC", "reduceOnly": True, "isTrigger": True, "orderType": "Stop Market", "side": "B", "triggerPx": "90", "sz": "1", "oid": 1}]
    assert coverage(exchange.snapshot())[0] == 0
