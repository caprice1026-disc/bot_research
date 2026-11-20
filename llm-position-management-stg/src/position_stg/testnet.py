"""Testnet-only execution with durable intents and continuous stop protection."""
import hashlib
import os
import time
import uuid
from .config import dec
from .domain import account, capacity, plan, track_risk
from .store import Lease, Conflict


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
        mode = self.info.query_user_abstraction_state(self.address)
        if mode == "unifiedAccount":
            spot = self.info.spot_user_state(self.address)
            usdc = next((b for b in spot.get("balances", []) if b.get("coin") == "USDC"), {})
            eq = dec(usdc.get("total", 0))
            available = eq - dec(usdc.get("hold", 0))
            for token, amount in spot.get("tokenToAvailableAfterMaintenance", []):
                if int(token) == 0:
                    available = dec(amount)
                    break
            collateral_source = "spotClearinghouseState"
        elif mode in {"default", "disabled", "dexAbstraction"}:
            eq = dec(state["marginSummary"]["accountValue"])
            available = dec(state.get("withdrawable", 0))
            collateral_source = "perpClearinghouseState"
        else:
            raise ValueError("unsupported Testnet account mode")
        if mark <= 0:
            raise ValueError("invalid BTC mark")
        return {"quantity": str(qty), "entry": str(entry), "equity": str(eq), "mark": str(mark),
                "step": str(dec(10) ** -meta["universe"][index]["szDecimals"]),
                "orders": self.info.frontend_open_orders(self.address), "account_mode": mode,
                "available_collateral": str(available), "collateral_source": collateral_source}

    def fills(self, since):
        return self.info.user_fills_by_time(self.address, int(since * 1000), aggregate_by_time=True)

    def funding(self, since):
        return self.info.user_funding_history(self.address, int(since * 1000))

    def configure_leverage(self, leverage=3, margin_mode=None, *, is_cross=None):
        if margin_mode is None:
            margin_mode = "cross" if is_cross is not False else "isolated"
        elif is_cross is not None and (margin_mode == "cross") != is_cross:
            raise ValueError("conflicting margin mode")
        if type(leverage) is not int or not 1 <= leverage <= 3 or margin_mode not in {"cross", "isolated"}:
            raise ValueError("Testnet leverage must be 1..3 with cross/isolated mode")
        result = self.exchange.update_leverage(leverage, "BTC", margin_mode == "cross")
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise RuntimeError("Testnet leverage update rejected")
        return result

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
        return self.exchange.order("BTC", buy, float(abs(delta)), px, order_type,
                                   reduce_only=reduce_only, cloid=Cloid.from_str(client_id))

    def cancel(self, oid):
        return self.exchange.cancel("BTC", oid)


def coverage(snapshot):
    qty, mark = dec(snapshot["quantity"]), dec(snapshot["mark"])
    valid = []
    for o in snapshot["orders"]:
        if (o.get("coin") == "BTC" and o.get("reduceOnly") and o.get("isTrigger")
                and "Stop" in o.get("orderType", "") and o.get("side") == ("A" if qty > 0 else "B")):
            stop = dec(o.get("triggerPx", 0))
            if stop > 0 and qty * (mark - stop) > 0 and dec(o.get("sz", 0)) > 0:
                valid.append(o)
    return min(abs(qty), sum((dec(o["sz"]) for o in valid), dec(0))), valid


class TestnetController:
    def __init__(self, cfg, store, exchange, *, clock=time.time):
        self.cfg, self.store, self.exchange, self.clock = cfg, store, exchange, clock
        self._emergency_allowed = False

    def _initial(self):
        return {"last_cycle": -1, "last_quantity": None, "manual_pause": False,
                "account": account(self.cfg.initial_equity), "stop": None, "position_owned": False,
                "pending_intents": [], "owned_oids": [], "seen_fill_keys": [], "last_reconciled": self.clock()}

    def _coverage(self, snapshot):
        owned = self.lease.data.get("owned_oids", [])
        return coverage({**snapshot, "orders": [o for o in snapshot["orders"] if int(o["oid"]) in owned]})

    def _fills(self, state):
        cutoff = state.get("fill_cutoff_ms", state.get("last_reconciled", self.clock()) * 1000) / 1000
        fills = self.exchange.fills(max(0, cutoff - 1))
        if len(fills) >= 500:
            state["manual_pause"] = True
            state["pause_reason"] = "fill_history_may_be_truncated"
            state["account"]["partial"] = True
        return fills

    def _stable_observation(self, state):
        """Consume fills only against a position read after those fills were read."""
        for _ in range(3):
            started = self.clock()
            before = self.exchange.snapshot()
            fills = self._fills(state)
            after = self.exchange.snapshot()
            if dec(before["quantity"]) == dec(after["quantity"]):
                self._snapshot_cutoff_ms = int(started * 1000)
                return after, fills
        raise RuntimeError("Testnet position changed throughout reconciliation")

    @staticmethod
    def _fill_key(fill):
        return str(fill.get("tid")) if fill.get("tid") is not None else ":".join(
            str(fill.get(k, "")) for k in ("oid", "time", "side", "sz", "px", "hash"))

    def _remember_fills(self, state, fills):
        state["seen_fill_keys"] = list(dict.fromkeys(state.get("seen_fill_keys", []) +
            [self._fill_key(f) for f in fills]))[-2000:]

    def reconcile_intents(self, state):
        unresolved, owned = [], set(state.get("owned_oids", []))
        for key in state.get("pending_intents", []):
            row, version = self.store.read(key)
            if not row:
                unresolved.append(key)
                continue
            if row.get("oid") is not None:
                owned.add(int(row["oid"]))
            if row.get("status") in {"rejected", "resolved", "protected"}:
                continue
            status = self.exchange.lookup(row["cloid"])
            detail = status.get("order", {}) if isinstance(status, dict) else {}
            order = detail.get("order", {})
            if "oid" in order:
                owned.add(int(order["oid"]))
            terminal = detail.get("status") or ""
            if terminal in {"filled", "canceled", "rejected"} or terminal.endswith(("Canceled", "Rejected")):
                row.update(status="resolved", resolution=terminal)
                self.store.write(key, row, version)
            elif terminal == "open" and row.get("stop"):
                row.update(status="protected", resolution="open")
                self.store.write(key, row, version)
            else:
                unresolved.append(key)
        state["pending_intents"], state["owned_oids"] = unresolved, sorted(owned)[-2000:]
        return not unresolved

    def submit(self, key, delta, snapshot, *, stop=None, reduce_only=False):
        old, version = self.store.read(key)
        if old:
            return {"status": "already_submitted", "lookup": self.exchange.lookup(old["cloid"])}
        intent = {"at": self.clock(), "cloid": cloid(self.cfg.bot_id + key), "delta": str(delta),
                  "mark": snapshot["mark"], "stop": str(stop) if stop is not None else None,
                  "reduce_only": reduce_only, "status": "sending"}
        version = self.store.write(key, intent, version)
        self.lease.data["pending_intents"] = list(dict.fromkeys(self.lease.data.get("pending_intents", []) + [key]))
        self.lease.save()
        self._emergency_allowed = True
        self._needs_fill_sync = True
        response = self.exchange.order(delta, dec(snapshot["mark"]), intent["cloid"], stop=stop, reduce_only=reduce_only)
        intent.update(status="submitted", response=response)
        if isinstance(response, dict) and response.get("status") == "err":
            intent["status"] = "rejected"
        statuses = response.get("response", {}).get("data", {}).get("statuses", []) if isinstance(response, dict) and isinstance(response.get("response"), dict) else []
        if statuses and all(isinstance(s, dict) and "error" in s for s in statuses):
            intent["status"] = "rejected"
        if intent["status"] == "rejected":
            self._execution_status = "order_rejected"
        if len(statuses) == 1 and isinstance(statuses[0], dict):
            for kind in ("filled", "resting"):
                detail = statuses[0].get(kind)
                if isinstance(detail, dict) and detail.get("oid") is not None:
                    intent["oid"] = int(detail["oid"])
                    if kind == "filled" or stop is not None:
                        intent.update(status="resolved" if kind == "filled" else "protected",
                                      resolution="filled" if kind == "filled" else "open")
        self.store.write(key, intent, version)
        self.reconcile_intents(self.lease.data)
        self.lease.save()
        return response

    def _cancel(self, oid, prefix):
        if int(oid) not in self.lease.data.get("owned_oids", []):
            raise ValueError("refusing to cancel an external order")
        key = f"cancel-{prefix}-{oid}"
        row, version = self.store.read(key)
        if row is not None:
            return
        row = {"at": self.clock(), "oid": int(oid), "status": "sending"}
        version = self.store.write(key, row, version)
        result = self.exchange.cancel(oid)
        row.update(status="submitted", response=result)
        self.store.write(key, row, version)

    def protect(self, snapshot, stop, prefix):
        started = time.monotonic()
        for attempt in range(2):
            qty = dec(snapshot["quantity"])
            if not qty:
                return snapshot
            protected, existing = self._coverage(snapshot)
            matching = [o for o in existing if abs(dec(o["triggerPx"]) - dec(stop)) <= dec(snapshot["mark"]) * dec(".0001")]
            if sum((dec(o["sz"]) for o in matching), dec(0)) >= abs(qty):
                return snapshot
            if time.monotonic() - started >= 20 or qty * (dec(snapshot["mark"]) - dec(stop)) <= 0:
                break
            try:
                self.submit(f"intent-{prefix}-sl-{attempt}", -qty, snapshot, stop=dec(stop), reduce_only=True)
                snapshot = self.exchange.snapshot()
                _, confirmed = self._coverage(snapshot)
                matching = [o for o in confirmed if abs(dec(o["triggerPx"]) - dec(stop)) <= dec(snapshot["mark"]) * dec(".0001")]
                if sum((dec(o["sz"]) for o in matching), dec(0)) >= abs(dec(snapshot["quantity"])):
                    for old in existing:
                        if old["oid"] not in {o["oid"] for o in matching}:
                            self._cancel(old["oid"], prefix)
                    return self.exchange.snapshot()
            except Conflict:
                raise
            except Exception:
                resolved = self.reconcile_intents(self.lease.data)
                snapshot = self.exchange.snapshot()
                if not resolved:
                    break
        protected, _ = self._coverage(snapshot)
        qty = dec(snapshot["quantity"])
        missing = max(dec(0), abs(qty) - protected)
        if missing:
            self.submit(f"intent-{prefix}-emergency", -missing.copy_sign(qty), snapshot, reduce_only=True)
            snapshot = self.exchange.snapshot()
        return snapshot

    def _observe_ownership(self, state, snapshot, fills):
        qty, previous = dec(snapshot["quantity"]), state.get("last_quantity")
        if previous is None:
            owned = qty == 0
            if qty:
                state.update(manual_pause=True, pause_reason="unknown_initial_position")
            else:
                a = state["account"]
                a.update(cash=snapshot["equity"], peak=snapshot["equity"], segment_peak=snapshot["equity"])
        else:
            owned = state.get("position_owned", True)
            if dec(previous) != qty:
                seen = set(state.get("seen_fill_keys", []))
                explained = sum((dec(f["sz"]) * (1 if f["side"] == "B" else -1)
                    for f in fills if f.get("coin") == "BTC" and int(f["oid"]) in state.get("owned_oids", [])
                    and self._fill_key(f) not in seen
                    and int(f["time"]) > state.get("fill_cutoff_ms", -1)), dec(0))
                if dec(previous) + explained != qty:
                    owned = False
                    state.update(manual_pause=True, pause_reason="unexplained_position_change")
        if any(int(o["oid"]) not in state.get("owned_oids", []) for o in snapshot["orders"]):
            state.update(manual_pause=True, pause_reason="external_open_order")
        if qty == 0:
            owned = True
        state["position_owned"] = owned
        self._emergency_allowed = owned and qty != 0
        self._remember_fills(state, fills)

    def _visible_intents(self, state, snapshot):
        """A returned client ID also establishes ownership if lookup is lagging."""
        by_id = {str(o.get("cloid")): o for o in snapshot["orders"] if o.get("cloid")}
        for key in list(state.get("pending_intents", [])):
            row, version = self.store.read(key)
            order = by_id.get(row.get("cloid")) if row else None
            if order is None:
                continue
            state["owned_oids"] = sorted(set(state["owned_oids"]) | {int(order["oid"])})[-2000:]
            if row.get("stop") is not None:
                row.update(status="protected", resolution="open", oid=int(order["oid"]))
                self.store.write(key, row, version)
                state["pending_intents"].remove(key)

    def _save_snapshot(self, snapshot):
        if self._needs_fill_sync:
            snapshot, fills = self._stable_observation(self.lease.data)
            self._remember_fills(self.lease.data, fills)
            self._needs_fill_sync = False
        state, now = self.lease.data, self.clock()
        qty, a = dec(snapshot["quantity"]), state["account"]
        _, stops = self._coverage(snapshot)
        effective = (min(dec(o["triggerPx"]) for o in stops) if qty > 0 else
                     max(dec(o["triggerPx"]) for o in stops)) if stops and qty else None
        stop = str(effective) if effective is not None else state.get("stop") if qty else None
        if dec(a["quantity"]) != qty or a["stop"] != stop:
            a["version"] += 1
        if qty and dec(a["quantity"]) == 0:
            a["opened_at"] = now
        if not qty:
            a["opened_at"] = None
        a.update(quantity=str(qty), entry=snapshot["entry"],
                 cash=str(dec(snapshot["equity"]) - qty * (dec(snapshot["mark"]) - dec(snapshot["entry"]))),
                 stop=stop, manual_pause=state["manual_pause"])
        state.update(stop=stop, last_quantity=str(qty), last_reconciled=now, snapshot=snapshot,
                     fill_cutoff_ms=self._snapshot_cutoff_ms)
        if dec(a["peak"]) > 0:
            track_risk(a, snapshot["mark"], now, self.cfg)
        self.lease.save()
        return snapshot

    def _safety(self, snapshot, prefix):
        state = self.lease.data
        if not state.get("position_owned", False):
            return self._save_snapshot(snapshot)
        qty = dec(snapshot["quantity"])
        protected, stops = self._coverage(snapshot)
        if qty:
            stop = state.get("stop")
            if stop is None and stops:
                stop = str(min(dec(o["triggerPx"]) for o in stops) if qty > 0 else max(dec(o["triggerPx"]) for o in stops))
                state["stop"] = stop
            if protected < abs(qty):
                if stop is not None:
                    snapshot = self.protect(snapshot, stop, prefix)
                else:
                    self.submit(f"intent-{prefix}-unprotected", -qty, snapshot, reduce_only=True)
                    snapshot = self.exchange.snapshot()
            snapshot = self._save_snapshot(snapshot)
            qty = dec(snapshot["quantity"])
            if qty and state.get("stop") is not None:
                if qty * (dec(snapshot["mark"]) - dec(state["stop"])) <= 0:
                    allowed = dec(0)
                else:
                    allowed = capacity(state["account"], snapshot["mark"], state["stop"],
                                       1 if qty > 0 else -1, snapshot["step"], self.cfg)
                if abs(qty) > allowed:
                    self.submit(f"intent-{prefix}-risk", allowed.copy_sign(qty) - qty, snapshot, reduce_only=True)
                    snapshot = self.exchange.snapshot()
                    if abs(dec(snapshot["quantity"])) > allowed:
                        self._execution_status = "risk_reduction_pending"
        if dec(snapshot["quantity"]) == 0:
            canceled = False
            for order in snapshot["orders"]:
                if int(order["oid"]) in state.get("owned_oids", []):
                    self._cancel(order["oid"], prefix)
                    canceled = True
            if canceled:
                snapshot = self.exchange.snapshot()
            if any(int(o["oid"]) in state.get("owned_oids", []) for o in snapshot["orders"]):
                self._execution_status = "cancel_pending"
        return self._save_snapshot(snapshot)

    def _result(self, snapshot, status=None):
        state = self.lease.data
        protected, _ = self._coverage(snapshot)
        problem = ("paused" if state["manual_pause"] else "pending_reconciliation" if state["pending_intents"] else
                   "unprotected" if protected < abs(dec(snapshot["quantity"])) else self._execution_status)
        status = problem or status or "checked"
        state["last_action_status"] = status
        self.lease.save()
        return {"status": status, "snapshot": snapshot, "manual_pause": state["manual_pause"],
                "pause_reason": state.get("pause_reason"), "position_owned": state.get("position_owned", False)}

    def emergency_without_db(self):
        if not self._emergency_allowed:
            return {"status": "db_unavailable", "emergency_attempted": False, "manual_pause": True}
        snapshot = self.exchange.snapshot()
        protected, _ = coverage(snapshot)
        qty = dec(snapshot["quantity"])
        missing = max(dec(0), abs(qty) - protected)
        if missing:
            self.exchange.order(-missing.copy_sign(qty), dec(snapshot["mark"]),
                cloid(f"emergency-{self.cfg.bot_id}-{uuid.uuid4().hex}"), reduce_only=True)
        return {"status": "db_unavailable", "emergency_attempted": bool(missing), "manual_pause": True,
                "snapshot": self.exchange.snapshot()}

    def _run(self, raw=None, scheduled_at=None, *, allow_increase=True, observed_mark=None, expected_quantity=None):
        self._emergency_allowed = False
        self._execution_status = None
        self._needs_fill_sync = False
        try:
            self.lease = Lease(self.store, self._initial(), clock=self.clock)
        except Conflict:
            raise
        except Exception:
            return self.emergency_without_db()
        state = self.lease.data
        prefix = f"maintenance-{uuid.uuid4().hex}"
        try:
            self.reconcile_intents(state)
            snapshot, fills = self._stable_observation(state)
            self._visible_intents(state, snapshot)
            self._observe_ownership(state, snapshot, fills)
            snapshot = self._safety(snapshot, prefix)
            if scheduled_at is None:
                return self._result(snapshot)
            if scheduled_at <= state["last_cycle"]:
                return self._result(snapshot, "duplicate")
            state["last_cycle"] = scheduled_at
            self.lease.save()
            if state["pending_intents"]:
                return self._result(snapshot, "pending_reconciliation")
            if expected_quantity is not None and dec(expected_quantity) != dec(snapshot["quantity"]):
                return self._result(snapshot, "stale_position")
            if not state.get("position_owned", False):
                return self._result(snapshot, "paused")
            if state["manual_pause"] or self.clock() - scheduled_at > self.cfg.max_start_delay_seconds:
                return self._result(snapshot, "paused" if state["manual_pause"] else "late")
            qty = dec(snapshot["quantity"])
            proposed = plan(state["account"], snapshot["mark"], snapshot["step"], raw, self.cfg,
                            allow_increase=allow_increase, observed_mark=observed_mark)
            target = dec(proposed["target"])
            if qty * target < 0:
                self.submit(f"intent-{scheduled_at}-close", -qty, snapshot, reduce_only=True)
                snapshot = self.exchange.snapshot()
                snapshot = self._save_snapshot(snapshot)
                if dec(snapshot["quantity"]) != 0:
                    return self._result(snapshot, "reversal_close_pending")
                snapshot = self._safety(snapshot, f"{scheduled_at}-reversal")
                if snapshot["orders"]:
                    return self._result(snapshot, "cancel_pending")
                qty = dec(0)
                target = min(abs(target), capacity(state["account"], snapshot["mark"], proposed["stop"],
                    1 if target > 0 else -1, snapshot["step"], self.cfg)).copy_sign(target)
            delta = target - qty
            if proposed["stop"] and (not qty or abs(target) >= abs(qty)):
                state["stop"] = proposed["stop"]
                self.lease.save()
            if delta:
                self.submit(f"intent-{scheduled_at}-delta", delta, snapshot, reduce_only=abs(target) < abs(qty))
                snapshot = self.exchange.snapshot()
                if dec(snapshot["quantity"]) != target and self._execution_status is None:
                    self._execution_status = "target_not_reached"
            if dec(snapshot["quantity"]):
                stop = proposed["stop"] if abs(dec(snapshot["quantity"])) <= abs(target) and proposed["stop"] else state["stop"]
                if stop is not None:
                    snapshot = self.protect(snapshot, stop, str(scheduled_at))
            snapshot = self._safety(snapshot, f"{scheduled_at}-final")
            return {**self._result(snapshot), "plan": proposed}
        except Exception:
            try:
                self.emergency_without_db()
            except Exception:
                pass
            raise
        finally:
            self.lease.release()

    def maintain(self):
        """Reconcile and protect without consuming a five-minute decision slot."""
        return self._run()

    def tick(self, raw, scheduled_at, *, allow_increase=True, observed_mark=None, expected_quantity=None):
        if scheduled_at > self.clock() + 2 or int(scheduled_at) % 300:
            raise ValueError("invalid Testnet schedule")
        return self._run(raw, scheduled_at, allow_increase=allow_increase,
                         observed_mark=observed_mark, expected_quantity=expected_quantity)


def from_env(cfg, store):
    if os.environ.get("ENABLE_TESTNET_ORDERS") != "true":
        raise ValueError("ENABLE_TESTNET_ORDERS=true required for explicit Testnet command")
    return TestnetController(cfg, store, TestnetExchange(os.environ["TESTNET_PRIVATE_KEY"], os.environ["TESTNET_ACCOUNT_ADDRESS"]))
