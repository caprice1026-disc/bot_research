"""Fault injection on a receipt-led toy exchange; not exchange integration."""
from __future__ import annotations
from dataclasses import dataclass, field
import copy
import numpy as np


@dataclass
class ToyExchange:
    price: float
    quantity: float = 0.
    orders: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)
    protections: dict = field(default_factory=dict)

    def submit(self, client_id, quantity):
        if client_id not in self.orders:
            self.orders[client_id] = {"quantity": quantity, "filled": 0., "cancelled": False}
        return self.orders[client_id]

    def fill(self, client_id, fraction=1.):
        order = self.orders[client_id]
        if order["cancelled"]:
            return
        delta = (order["quantity"]-order["filled"])*fraction
        if abs(delta)<1e-12:
            return
        fill_id = f"{client_id}:fill:{len(self.fills)}"
        self.fills.append(dict(fill_id=fill_id, quantity=delta, price=self.price, fee=abs(delta)*self.price*.0005))
        self.quantity += delta
        order["filled"] += delta

    def snapshot(self):
        return copy.deepcopy(dict(quantity=self.quantity, orders=self.orders, fills=self.fills, protections=self.protections))


def receipt_account(fills):
    """Unique exchange fill IDs are primary; duplicate notifications do not add PnL."""
    unique={}
    for fill in fills:
        key=fill["fill_id"]
        if key in unique and unique[key] != fill:
            raise ValueError("conflicting receipts")
        unique[key]=fill
    return dict(quantity=sum(x["quantity"] for x in unique.values()), fees=sum(x["fee"] for x in unique.values()))


def recover(exchange, available=True):
    if not available:
        return dict(status="halted_unavailable", new_order=False)
    snapshot=exchange.snapshot()
    account=receipt_account(snapshot["fills"])
    if abs(account["quantity"]-snapshot["quantity"])>1e-10:
        return dict(status="halted_ledger_mismatch", new_order=False)
    for order in exchange.orders.values():
        if abs(order["quantity"]-order["filled"])>1e-10:
            order["cancelled"]=True
    q=account["quantity"]
    if abs(q)>1e-10:
        # Deterministic child IDs and reduce-only intended quantity.
        exchange.protections={kind:dict(client_id=f"recovery:{kind}",quantity=-q,reduce_only=True) for kind in ("SL","TP")}
    else:
        exchange.protections={}
    return dict(status="reconciled", new_order=False, **account)


def faults(market, execution, seed=20261031):
    rng=np.random.default_rng(seed)
    chosen=rng.choice(execution,size=min(100,len(execution)),replace=False)
    rows=[]
    for scenario in ("after_fill_before_ack","partial_fill","SL_registered_TP_missing"):
        for index in chosen:
            price=float(market["open"][index]); target=1000./price
            original=ToyExchange(price)
            original.submit("parent",target)
            original.fill("parent",.5 if scenario=="partial_fill" else 1.)
            if scenario=="SL_registered_TP_missing":
                original.protections["SL"]=dict(client_id="parent:SL",quantity=-target,reduce_only=True)
            intended=original.quantity
            for mode in ("blind_retry","receipt_reconcile"):
                ex=copy.deepcopy(original)
                if mode=="blind_retry":
                    ex.submit("retry-new-id",target); ex.fill("retry-new-id")
                    # All remaining accepted parent orders can subsequently fill.
                    ex.fill("parent")
                else:
                    status=recover(ex)
                    before=ex.snapshot(); recover(ex)
                    assert before==ex.snapshot(), "recovery is not idempotent"
                    assert status["status"]=="reconciled"
                    # A delayed fill notification cannot double-count the ledger.
                    assert receipt_account(ex.fills+ex.fills)==receipt_account(ex.fills)
                    for key in list(ex.orders): ex.fill(key)
                protected=all(kind in ex.protections and abs(ex.protections[kind]["quantity"]+ex.quantity)<1e-10 and ex.protections[kind]["reduce_only"] for kind in ("SL","TP"))
                rows.append(dict(scenario=scenario,mode=mode,timestamp=int(market["timestamp"][index]),
                                 unintended_notional=abs(ex.quantity-intended)*price, missing_protection=not protected,
                                 fill_count=len(ex.fills), fees=receipt_account(ex.fills)["fees"]))
    unavailable=ToyExchange(100.)
    before=unavailable.snapshot()
    assert recover(unavailable,False)["status"]=="halted_unavailable" and before==unavailable.snapshot()
    mismatch=ToyExchange(100.,quantity=1.)
    assert recover(mismatch)["status"]=="halted_ledger_mismatch"
    return rows
