"""Explicit Testnet execution, durable intent IDs and reduce-only emergency fallback.

This adapter never constructs a Mainnet Exchange client. Use a dedicated Testnet account.
"""
import hashlib
import os
import time
from .config import dec
from .domain import account, capacity, equity, floor_qty, plan, track_risk
from .store import Lease


def cloid(intent):
    return "0x" + hashlib.blake2b(intent.encode(), digest_size=16).hexdigest()


class TestnetExchange:
    def __init__(self, private_key, address):
        from eth_account import Account
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils.constants import TESTNET_API_URL
        self.info = Info(TESTNET_API_URL, skip_ws=True, timeout=10)
        self.exchange = Exchange(Account.from_key(private_key), TESTNET_API_URL, account_address=address, timeout=10)
        self.address = address

    def snapshot(self):
        state = self.info.user_state(self.address)
        rows = [x["position"] for x in state["assetPositions"]]
        if any(r["coin"] != "BTC" and dec(r["szi"]) != 0 for r in rows):
            raise ValueError("dedicated BTC Testnet account required")
        position = next((r for r in rows if r["coin"] == "BTC"), {})
        qty, entry = dec(position.get("szi", 0)), dec(position.get("entryPx") or 0)
        meta, contexts = self.info.meta_and_asset_ctxs()
        index = next(i for i, a in enumerate(meta["universe"]) if a["name"] == "BTC")
        mark = dec(contexts[index]["markPx"])
        eq = dec(state["marginSummary"]["accountValue"])
        orders = self.info.frontend_open_orders(self.address)
        return {"quantity": str(qty), "entry": str(entry), "equity": str(eq), "mark": str(mark),
                "step": str(dec(10) ** -meta["universe"][index]["szDecimals"]), "orders": orders}

    def fills(self, since):
        return self.info.user_fills_by_time(self.address, int(since * 1000), aggregate_by_time=True)

    def lookup(self, client_id):
        from hyperliquid.utils.types import Cloid
        return self.info.query_order_by_cloid(self.address, Cloid.from_str(client_id))

    def order(self, delta, mark, client_id, *, stop=None, reduce_only=False):
        from hyperliquid.utils.types import Cloid
        buy = delta > 0
        px = float(f"{float(mark * (dec('1.005') if buy else dec('.995'))):.5g}")
        order_type = {"limit": {"tif": "Ioc"}}
        if stop is not None:
            trigger = float(f"{float(stop):.5g}")
            order_type = {"trigger": {"triggerPx": trigger, "isMarket": True, "tpsl": "sl"}}
            px = trigger
        return self.exchange.order("BTC", buy, float(abs(delta)), px, order_type, reduce_only=reduce_only, cloid=Cloid.from_str(client_id))

    def cancel(self, oid):
        return self.exchange.cancel("BTC", oid)


def coverage(snapshot):
    qty, mark = dec(snapshot["quantity"]), dec(snapshot["mark"])
    valid = []
    for o in snapshot["orders"]:
        if (o.get("coin") == "BTC" and o.get("reduceOnly") and o.get("isTrigger")
                and "Stop" in o.get("orderType", "") and o.get("side") == ("A" if qty > 0 else "B")):
            stop = dec(o.get("triggerPx", 0))
            if stop > 0 and qty * (mark - stop) > 0:
                valid.append(o)
    protected = min(abs(qty), sum((dec(o["sz"]) for o in valid), dec(0)))
    return protected, valid


class TestnetController:
    def __init__(self, cfg, store, exchange, *, clock=time.time):
        self.cfg, self.store, self.exchange, self.clock = cfg, store, exchange, clock

    def submit(self, key, delta, snapshot, *, stop=None, reduce_only=False):
        old, version = self.store.read(key)
        if old:
            result = self.exchange.lookup(old["cloid"])
            # An unknown API result never authorizes a retry of a submitted order.
            return {"status": "already_submitted", "lookup": result}
        intent = {"at": self.clock(), "cloid": cloid(self.cfg.bot_id + key), "delta": str(delta),
                  "mark": snapshot["mark"], "stop": str(stop) if stop is not None else None,
                  "reduce_only": reduce_only, "status": "sending"}
        version = self.store.write(key, intent, version)
        if hasattr(self, "lease"):
            self.lease.data["pending_intents"] = list(dict.fromkeys(self.lease.data.get("pending_intents", []) + [key]))
            self.lease.save()
        response = self.exchange.order(delta, dec(snapshot["mark"]), intent["cloid"], stop=stop, reduce_only=reduce_only)
        intent.update(status="submitted", response=response)
        self.store.write(key, intent, version)
        return response

    def protect(self, snapshot, stop, prefix):
        started = self.clock()
        for attempt in range(2):
            qty = dec(snapshot["quantity"])
            if not qty:
                return snapshot
            protected, existing = coverage(snapshot)
            matching = [o for o in existing if abs(dec(o["triggerPx"]) - dec(stop)) <= dec(snapshot["mark"]) * dec(".0001")]
            if sum((dec(o["sz"]) for o in matching), dec(0)) >= abs(qty):
                return snapshot
            if self.clock() - started >= 20:
                break
            try:
                # Place replacement before cancellation; a crash retains protection.
                self.submit(f"intent-{prefix}-sl-{attempt}", -qty, snapshot, stop=dec(stop), reduce_only=True)
                snapshot = self.exchange.snapshot()
                protected, confirmed = coverage(snapshot)
                matching = [o for o in confirmed if abs(dec(o["triggerPx"]) - dec(stop)) <= dec(snapshot["mark"]) * dec(".0001")]
                if sum((dec(o["sz"]) for o in matching), dec(0)) >= abs(dec(snapshot["quantity"])):
                    for old in existing:
                        if old["oid"] not in {o["oid"] for o in matching} and old["oid"] in self.lease.data.get("owned_oids", []):
                            self.exchange.cancel(old["oid"])
                    return self.exchange.snapshot()
            except Exception:
                snapshot = self.exchange.snapshot()
        protected, _ = coverage(snapshot)
        qty = dec(snapshot["quantity"])
        missing = max(dec(0), abs(qty) - protected)
        if missing:
            try:
                self.submit(f"intent-{prefix}-emergency", -missing.copy_sign(qty), snapshot, reduce_only=True)
            except Exception:
                self.emergency_without_db()
            snapshot = self.exchange.snapshot()
        return snapshot

    def reconcile_intents(self, state):
        unresolved = []
        owned = set(state.get("owned_oids", []))
        for key in state.get("pending_intents", []):
            row, version = self.store.read(key)
            if not row:
                unresolved.append(key)
                continue
            status = self.exchange.lookup(row["cloid"])
            detail = status.get("order", {}) if isinstance(status, dict) else {}
            order = detail.get("order", {})
            if "oid" in order:
                owned.add(int(order["oid"]))
            terminal = detail.get("status")
            if terminal in {"filled", "canceled", "rejected", "marginCanceled", "reduceOnlyCanceled", "siblingFilledCanceled"}:
                row.update(status="resolved", resolution=terminal)
                self.store.write(key, row, version)
            elif terminal == "open" and row.get("stop"):
                row.update(status="protected", resolution="open")
                self.store.write(key, row, version)
            else:
                unresolved.append(key)
        state["pending_intents"] = unresolved
        state["owned_oids"] = sorted(owned)[-1000:]
        return not unresolved

    def emergency_without_db(self):
        snapshot = self.exchange.snapshot()
        protected, _ = coverage(snapshot)
        qty = dec(snapshot["quantity"])
        missing = max(dec(0), abs(qty) - protected)
        if missing:
            # No durable sender is available: single reduce-only attempt, never retry blindly.
            self.exchange.order(-missing.copy_sign(qty), dec(snapshot["mark"]), cloid(f"emergency-{self.cfg.bot_id}-{self.clock()}"), reduce_only=True)
        return {"status": "db_unavailable", "emergency_attempted": bool(missing)}

    def tick(self, raw, scheduled_at):
        if scheduled_at > self.clock() + 2 or int(scheduled_at) % 300:
            raise ValueError("invalid Testnet schedule")
        initial = {"last_cycle": -1, "last_quantity": None, "manual_pause": False,
                   "account": account(self.cfg.initial_equity), "stop": None,
                   "pending_intents": [], "owned_oids": [], "last_reconciled": self.clock()}
        try:
            lease = Lease(self.store, initial, clock=self.clock)
        except Exception as exc:
            from .store import Conflict
            if isinstance(exc, Conflict):
                raise
            return self.emergency_without_db()
        state = lease.data
        self.lease = lease
        try:
            snapshot = self.exchange.snapshot()
            qty = dec(snapshot["quantity"])
            resolved = self.reconcile_intents(state)
            if state["last_quantity"] is not None and dec(state["last_quantity"]) != qty:
                fills = self.exchange.fills(state["last_reconciled"])
                explained = sum((dec(f["sz"]) * (1 if f["side"] == "B" else -1)
                                 for f in fills if f["coin"] == "BTC" and int(f["oid"]) in state["owned_oids"]
                                 and int(f["time"]) > state["last_reconciled"] * 1000), dec(0))
                if dec(state["last_quantity"]) + explained != qty:
                    state["manual_pause"] = True
            if any(o["coin"] == "BTC" and int(o["oid"]) not in state["owned_oids"] for o in snapshot["orders"]):
                state["manual_pause"] = True
            protected, stops = coverage(snapshot)
            if qty and protected < abs(qty):
                if state["stop"] is not None:
                    snapshot = self.protect(snapshot, state["stop"], f"{scheduled_at}-recovery")
                else:
                    self.submit(f"intent-{scheduled_at}-unprotected", -qty, snapshot, reduce_only=True)
                    snapshot = self.exchange.snapshot()
            # Protection recovery must run even for a duplicate or interrupted cycle.
            if scheduled_at <= state["last_cycle"] or not resolved:
                lease.save()
                return {"status": "duplicate" if resolved else "pending_reconciliation", "snapshot": snapshot}
            state["last_cycle"] = scheduled_at
            lease.save()
            a = state["account"]
            qty = dec(snapshot["quantity"])
            a.update(quantity=str(qty), entry=snapshot["entry"],
                     cash=str(dec(snapshot["equity"]) - qty * (dec(snapshot["mark"]) - dec(snapshot["entry"]))),
                     stop=state["stop"], manual_pause=state["manual_pause"])
            if a["stop"] is None and stops:
                a["stop"] = str(max(dec(o["triggerPx"]) for o in stops) if qty > 0 else min(dec(o["triggerPx"]) for o in stops))
            track_risk(a, snapshot["mark"], self.clock(), self.cfg)
            # Risk-only hold still forces excess exposure down while paused.
            if state["manual_pause"] or self.clock() - scheduled_at > self.cfg.max_start_delay_seconds:
                raw = {"intent": "hold", "target_multiple": None, "stop_price": None, "reason": "paused"}
            proposed = plan(a, snapshot["mark"], snapshot["step"], raw, self.cfg, allow_increase=not state["manual_pause"])
            target = dec(proposed["target"])
            if qty * target < 0:
                self.submit(f"intent-{scheduled_at}-close", -qty, snapshot, reduce_only=True)
                snapshot = self.exchange.snapshot()
                if dec(snapshot["quantity"]) != 0:
                    return {"status": "reversal_close_pending"}
                for order in snapshot["orders"]:
                    if order["coin"] == "BTC":
                        self.exchange.cancel(order["oid"])
                snapshot = self.exchange.snapshot()
                if any(o["coin"] == "BTC" for o in snapshot["orders"]):
                    return {"status": "cancel_pending"}
                qty = dec(0)
                a.update(quantity="0", entry="0", cash=snapshot["equity"])
                target = min(abs(target), capacity(a, snapshot["mark"], proposed["stop"], 1 if target > 0 else -1, snapshot["step"], self.cfg)).copy_sign(target)
            delta = target - qty
            # Save the intended protection before sending any order that could fill.
            if proposed["stop"] and (not qty or abs(target) >= abs(qty)):
                state["stop"] = proposed["stop"]
                lease.save()
            if delta:
                self.submit(f"intent-{scheduled_at}-delta", delta, snapshot, reduce_only=abs(target) < abs(qty))
                snapshot = self.exchange.snapshot()
            # Only loosen after the requested reduction is confirmed.
            if dec(snapshot["quantity"]):
                if abs(dec(snapshot["quantity"])) <= abs(target) and proposed["stop"]:
                    snapshot = self.protect(snapshot, proposed["stop"], str(scheduled_at))
                elif state["stop"]:
                    snapshot = self.protect(snapshot, state["stop"], str(scheduled_at))
                else:
                    self.submit(f"intent-{scheduled_at}-partial-emergency", -dec(snapshot["quantity"]), snapshot, reduce_only=True)
                    snapshot = self.exchange.snapshot()
            actual_qty = dec(snapshot["quantity"])
            _, actual_orders = coverage(snapshot)
            if actual_qty and actual_orders:
                # A rejected replacement cannot become the recorded effective stop.
                effective = min(dec(o["triggerPx"]) for o in actual_orders) if actual_qty > 0 else max(dec(o["triggerPx"]) for o in actual_orders)
                state["stop"] = str(effective)
                a.update(quantity=str(actual_qty), entry=snapshot["entry"],
                         cash=str(dec(snapshot["equity"]) - actual_qty * (dec(snapshot["mark"]) - dec(snapshot["entry"]))), stop=str(effective))
                cap = capacity(a, snapshot["mark"], effective, 1 if actual_qty > 0 else -1, snapshot["step"], self.cfg)
                if abs(actual_qty) > cap:
                    self.submit(f"intent-{scheduled_at}-actual-stop-risk", cap.copy_sign(actual_qty) - actual_qty, snapshot, reduce_only=True)
                    snapshot = self.exchange.snapshot()
            elif not actual_qty:
                state["stop"] = None
                for order in snapshot["orders"]:
                    if order["coin"] == "BTC" and order["oid"] in state.get("owned_oids", []):
                        self.exchange.cancel(order["oid"])
            state["last_quantity"] = snapshot["quantity"]
            state["last_reconciled"] = self.clock()
            lease.save()
            protected, _ = coverage(snapshot)
            status = "unprotected" if protected < abs(dec(snapshot["quantity"])) else "paused" if state["manual_pause"] else "checked"
            return {"status": status, "snapshot": snapshot}
        except Exception:
            # Covers response loss and DB loss after a fill, not only failure to acquire the lease.
            try:
                self.emergency_without_db()
            except Exception:
                pass  # Connection itself may be unavailable; next tick reconciles before trading.
            raise
        finally:
            lease.release()


def from_env(cfg, store):
    if os.environ.get("ENABLE_TESTNET_ORDERS") != "true":
        raise ValueError("ENABLE_TESTNET_ORDERS=true required for explicit Testnet command")
    return TestnetController(cfg, store, TestnetExchange(os.environ["TESTNET_PRIVATE_KEY"], os.environ["TESTNET_ACCOUNT_ADDRESS"]))
