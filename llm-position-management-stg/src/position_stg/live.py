"""Continuous Testnet orchestration; every exchange action stays in its controller.

The live ledger is separate from the shadow simulator and controller runtime.
An attempted five-minute slot is never replayed, including after interruption.
"""
from copy import deepcopy
import hashlib
import json
import threading
import time
import uuid

from .config import dec
from .domain import parse_decision
from .policy import validate_review
from .store import Conflict


DECISION_SECONDS = 300
REVIEW_SECONDS = 7200
PROTECTION_SECONDS = 15
HISTORY_OVERLAP_SECONDS = 7200
HISTORY_RESCAN_SECONDS = 7200
SAFE_MAINTENANCE = {"checked"}


class LiveSafetyError(RuntimeError):
    pass


def _error(phase, exc):
    # Provider exception messages can contain credentials or request URLs.
    return {"phase": phase, "error_type": type(exc).__name__}


class LiveRunner:
    def __init__(self, cfg, store, market, policy, controller, *, clock=time.time):
        self.cfg, self.store, self.market = cfg, store, market
        self.policy, self.controller, self.clock = policy, controller, clock
        self._pending = None
        self._stop_event = None

    def _state(self):
        state, version = self.store.read("live-runtime")
        if state is None:
            now = self.clock()
            state = {"schema": 1, "network": "testnet", "bot_id": self.cfg.bot_id,
                     "started_at": now, "config_hash": self.cfg.fingerprint(),
                     "strategy": None, "strategy_version": 0, "last_review": None,
                     "last_review_attempt": None, "recent_decisions": [],
                     "fill_cursor": now, "funding_cursor": now, "audit_health": {},
                     "history_cursor_schema": 2}
            try:
                self.store.write("live-runtime", state, 0)
            except Conflict:
                return self._state()
        if (state.get("schema") != 1 or state.get("network") != "testnet"
                or state.get("bot_id") != self.cfg.bot_id
                or state.get("config_hash") != self.cfg.fingerprint()):
            raise LiveSafetyError("live state/configuration mismatch")
        return state

    def _update(self, **values):
        state, version = self.store.read("live-runtime")
        if state is None:
            raise LiveSafetyError("missing live runtime")
        state.update(deepcopy(values))
        self.store.write("live-runtime", state, version)
        return state

    def _put(self, key, value):
        old, version = self.store.read(key)
        if old is None:
            self.store.write(key, value, version)
        elif old != value:
            raise LiveSafetyError("historical event changed")

    def _collect_history(self):
        """Fetch overlapping windows and deduplicate stable provider identities.

        Cursors are observed event timestamps, never request completion times.
        Empty responses cannot seal an interval. Overlap catches reordered events;
        periodic full scans catch later publication outside the overlap window.
        Full responses remain incomplete until pagination/retention is resolved.
        """
        state = self._state()
        if state.get("history_cursor_schema") != 2:
            # Upgrade old wall-clock cursors without assuming their interval was complete.
            self._update(history_cursor_schema=2, fill_cursor=state["started_at"],
                         funding_cursor=state["started_at"], fill_rescan=None, funding_rescan=None)
        for kind, method in (("fill", "fills"), ("funding", "funding")):
            state = self._state()
            until = self.clock()
            prior_rescan = state.get(kind + "_rescan")
            rescan = prior_rescan is None or until - prior_rescan["attempted_at"] >= HISTORY_RESCAN_SECONDS
            since = state["started_at"] if rescan else max(state["started_at"], state[kind + "_cursor"] - HISTORY_OVERLAP_SECONDS)
            health = deepcopy(state["audit_health"])
            try:
                rows = getattr(self.controller.exchange, method)(since)
                if not isinstance(rows, list):
                    raise ValueError("history must be a list")
                partial = len(rows) >= 500
                observed_until = dec(state[kind + "_cursor"])
                for raw in rows:
                    stamp = dec(raw["time"]) / 1000
                    if stamp < dec(state["started_at"]):
                        continue
                    if stamp > dec(self.clock()) + 2:
                        raise ValueError("future history event")
                    detail = raw if kind == "fill" else raw.get("delta", raw)
                    if detail.get("coin") != "BTC":
                        continue
                    if kind == "fill":
                        if raw.get("tid") is None or raw.get("oid") is None:
                            raise ValueError("missing stable fill identity")
                        identity = [raw["time"], raw["tid"], raw["oid"], raw["coin"]]
                    else:
                        if detail.get("type", "funding") != "funding":
                            continue
                        identity = [raw["time"], raw.get("hash"), detail["coin"]]
                    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                    self._put("live-" + kind + "-" + digest,
                              {"at": float(stamp), "network": "testnet", "raw": raw})
                    observed_until = max(observed_until, stamp)
                rescan_result = ({"attempted_at": until, "status": "possibly_truncated" if partial else "observed",
                                  "rows": len(rows)} if rescan else prior_rescan)
                rescan_unresolved = rescan_result is None or rescan_result["status"] != "observed"
                health[kind] = {"status": "possibly_truncated" if partial else "reconciliation_incomplete" if rescan_unresolved else "observed",
                                "since": since, "until": until, "rows": len(rows),
                                "cursor_basis": "observed_event_time", "full_rescan": rescan,
                                "overlap_seconds": HISTORY_OVERLAP_SECONDS,
                                "full_rescan_result": rescan_result, "provider_retention_verified": False}
                updates = {"audit_health": health}
                if not partial:
                    updates[kind + "_cursor"] = float(observed_until)
                if rescan:
                    updates[kind + "_rescan"] = rescan_result
                self._update(**updates)
            except Exception as exc:
                health[kind] = {"status": "error", "since": since, "until": until,
                                "full_rescan": rescan, "error_type": type(exc).__name__}
                updates = {"audit_health": health}
                if rescan:
                    updates[kind + "_rescan"] = {"attempted_at": until, "status": "error",
                                                "error_type": type(exc).__name__}
                self._update(**updates)

    def maintain(self):
        """Protect/reconcile without consuming a decision slot, then observe facts."""
        try:
            self._state()
        except Exception:
            # A broken reporting ledger must not disable exchange protection.
            try:
                self.controller.maintain()
            except Exception:
                pass
            raise
        at = self.clock()
        key = "live-maintenance-" + str(int(at * 1000)) + "-" + uuid.uuid4().hex[:8]
        try:
            result = self.controller.maintain()
            self.store.write(key, {"at": at, **deepcopy(result)}, 0)
            snapshot = result.get("snapshot")
            if snapshot is not None:
                self.store.write("live-account-" + key.removeprefix("live-maintenance-"),
                                 {"at": at, "snapshot": deepcopy(snapshot)}, 0)
            self._update(last_maintenance={"at": at, "status": result.get("status", "unknown"),
                                           "manual_pause": bool(result.get("manual_pause"))})
            self._collect_history()
            return result
        except Exception as exc:
            try:
                previous, version = self.store.read(key)
                self.store.write(key, {**(previous or {}), "at": at, "status": "error",
                                       **_error("maintenance", exc)}, version)
                self._update(last_maintenance_error={"at": at, **_error("maintenance", exc)})
            except Exception:
                pass
            raise

    def _policy_call(self, role, slot, inputs, event):
        request_id = "live-" + role + "-" + str(int(slot))
        event[role + "_request_id"] = request_id
        if self.store.read("llm-" + request_id)[0] is not None:
            raise LiveSafetyError("model request already attempted")
        if self._pending is not None and self._pending.is_alive():
            raise LiveSafetyError("previous model request still pending")
        finished = threading.Event()
        result = {}

        def call():
            try:
                result["value"] = self.policy.call(role, request_id, inputs)
            except BaseException as exc:
                result["exception"] = exc
            finally:
                finished.set()

        worker = threading.Thread(target=call, daemon=True, name="testnet-model-call")
        self._pending = worker
        worker.start()
        deadline = time.monotonic() + getattr(self.cfg, role + "_timeout_seconds")
        safe = True
        while not finished.wait(min(PROTECTION_SECONDS, max(0, deadline - time.monotonic()))):
            try:
                maintenance = self.maintain()
                safe = safe and maintenance.get("status") in SAFE_MAINTENANCE and not maintenance.get("manual_pause")
            except Exception as exc:
                event["errors"].append(_error("maintenance_during_" + role, exc))
                safe = False
            if self._stop_requested():
                raise LiveSafetyError("stop requested while model call pending")
            if time.monotonic() >= deadline:
                raise TimeoutError("model response deadline exceeded")
        if "exception" in result:
            raise result["exception"]
        event[role + "_response"] = deepcopy(result["value"])
        if time.monotonic() > deadline:
            raise TimeoutError("model response arrived after deadline")
        if self._stop_requested():
            raise LiveSafetyError("stop requested; model response not applied")
        if not safe:
            raise LiveSafetyError("maintenance failed during model call")
        return result["value"]

    def _stop_requested(self):
        control = self.store.read("live-control")[0] or {}
        return bool(control.get("stop_requested") or (self._stop_event is not None and self._stop_event.is_set()))

    def _observation(self):
        features = self.market.features(self.clock())
        market = self.market.snapshot()
        age = self.clock() - market["at"]
        if age < -2 or age > self.cfg.book_max_age_seconds:
            raise ValueError("stale market")
        return market, features

    def _fresh_strategy(self, state):
        return (state["last_review"] is not None
                and (state["last_review_attempt"] is None or state["last_review_attempt"] <= state["last_review"])
                and 0 <= self.clock() - state["last_review"] <= self.cfg.strategy_ttl_seconds)

    def _review_evidence(self, state):
        since = state["last_review"] if state["last_review"] is not None else state["started_at"]
        fills = [r for _, r, _ in self.store.items("live-fill-") if r["at"] >= since]
        funding = [r for _, r, _ in self.store.items("live-funding-") if r["at"] >= since]
        fills.sort(key=lambda r: r["at"])
        funding.sort(key=lambda r: r["at"])
        cumulative = summary(self.store)
        fill_fields = {"time", "coin", "side", "px", "sz", "closedPnl", "fee", "feeToken", "tid", "oid"}
        return {"since": since, "until": self.clock(), "window_kind": "provider_event_time",
                "fills": [{k: v for k, v in r["raw"].items() if k in fill_fields} for r in fills[-20:]],
                "funding": [r["raw"] for r in funding[-20:]],
                "window_fill_count": len(fills), "window_funding_count": len(funding),
                "samples_truncated": len(fills) > 20 or len(funding) > 20,
                "cumulative_observed": {k: cumulative[k] for k in (
                    "observed_fills", "observed_funding_events", "observed_closed_pnl_usdc",
                    "observed_fees_usdc", "observed_funding_usdc", "observed_realized_net_usdc",
                    "accounting_status", "history", "api_estimated_known_usd", "api_unknown_reserved_usd")},
                "note": "Cumulative totals include late-discovered events; samples are bounded and history may be incomplete."}

    def run(self, scheduled_at):
        now = self.clock()
        if scheduled_at > now + 2 or int(scheduled_at) != scheduled_at or int(scheduled_at) % DECISION_SECONDS:
            raise ValueError("scheduled_at must be a nonfuture five-minute boundary")
        state = self._state()
        slot = int(scheduled_at)
        key = "live-cycle-" + str(slot)
        previous, version = self.store.read(key)
        if previous is not None:
            maintenance = self.maintain()
            return {"status": "duplicate", "previous_status": previous["status"],
                    "maintenance_status": maintenance.get("status")}
        event = {"at": now, "scheduled_at": slot, "network": "testnet", "status": "started",
                 "config_hash": self.cfg.fingerprint(), "errors": [],
                 "news": [], "news_status": "disabled", "decision_status": "not_attempted"}
        try:
            version = self.store.write(key, event, version)
        except Conflict:
            return {"status": "duplicate", "previous_status": "claimed_concurrently"}
        phase = "maintenance"
        try:
            before = self.maintain()
            event["before"] = deepcopy(before)
            if self._stop_requested():
                event.update(status="skipped", decision_status="stop_requested")
                return event
            if before.get("status") not in SAFE_MAINTENANCE or before.get("manual_pause"):
                event.update(status="blocked", decision_status="maintenance_not_ready")
                return event
            if now - scheduled_at > min(60, self.cfg.max_start_delay_seconds):
                event.update(status="skipped", decision_status="late_start")
                return event
            phase = "market"
            market, features = self._observation()
            event["market_before"] = deepcopy(market)
            event["features"] = features
            state = self._state()
            due = state["last_review_attempt"] is None or self.clock() - state["last_review_attempt"] >= REVIEW_SECONDS
            if due:
                phase = "reviewer"
                self._update(last_review_attempt=self.clock())
                raw = self._policy_call("reviewer", slot, {
                    "market": market, "features": features, "account": before["snapshot"],
                    "strategy": state["strategy"], "news": [], "news_status": "disabled",
                    "recent_decisions": state["recent_decisions"][-24:],
                    "execution_evidence": self._review_evidence(state),
                }, event)
                strategy = validate_review(raw, [])
                strategy_version = state["strategy_version"] + 1
                strategy = {**strategy, "version": strategy_version, "updated_at": self.clock()}
                state = self._update(strategy=strategy, strategy_version=strategy_version,
                                     last_review=self.clock())
                event["review"] = deepcopy(strategy)
                # Review latency and protection may change both market and account.
                before = self.maintain()
                event["before_trader"] = deepcopy(before)
                if before.get("status") not in SAFE_MAINTENANCE or before.get("manual_pause"):
                    raise LiveSafetyError("maintenance not ready after review")
                market, features = self._observation()
            event["strategy"] = deepcopy(state["strategy"])
            if not self._fresh_strategy(state):
                event.update(status="blocked", decision_status="strategy_expired_or_missing")
                return event
            if self.clock() - scheduled_at > min(60, self.cfg.max_start_delay_seconds):
                event.update(status="skipped", decision_status="late_after_review")
                return event
            phase = "trader"
            runtime = self.store.read("runtime")[0] or {}
            inputs = {"market": market, "features": features,
                      "account": deepcopy(runtime.get("account", before["snapshot"])),
                      "account_snapshot": before["snapshot"], "equity": before["snapshot"]["equity"],
                      "strategy": state["strategy"], "recent_decisions": state["recent_decisions"][-6:],
                      "risk": {"max_exposure": str(self.cfg.max_exposure), "stop_risk": str(self.cfg.stop_risk)}}
            raw = self._policy_call("trader", slot, inputs, event)
            parse_decision(raw)
            event["proposal"], event["reason"] = deepcopy(raw), raw["reason"]
            phase = "execution"
            applied = self.controller.tick(raw, slot, allow_increase=self._fresh_strategy(self._state()),
                                           observed_mark=market["mark"], expected_quantity=before["snapshot"]["quantity"])
            event["execution"] = deepcopy(applied)
            event["decision_status"] = applied.get("status", "unknown")
            event["status"] = "complete" if applied.get("status") in {"checked", "complete"} else "partial"
            return event
        except Exception as exc:
            event["errors"].append(_error(phase, exc))
            event["status"] = "error"
            event["decision_status"] = phase + "_failed"
            return event
        finally:
            try:
                event["after"] = self.maintain()
                if (event["after"].get("status") not in SAFE_MAINTENANCE or event["after"].get("manual_pause")) and event["status"] == "complete":
                    event["status"] = "partial"
            except Exception as exc:
                event["errors"].append(_error("final_maintenance", exc))
                event["status"] = "error"
            event["finished_at"] = self.clock()
            audit = self._state().get("audit_health", {})
            event["audit_health"] = deepcopy(audit)
            for kind in ("fill", "funding"):
                if audit.get(kind, {}).get("status") != "observed":
                    event["errors"].append({"phase": kind + "_history", **audit.get(kind, {"status": "unavailable"})})
                    if event["status"] == "complete":
                        event["status"] = "partial"
            self.store.write(key, event, version)
            state = self._state()
            recent = {k: deepcopy(event.get(k)) for k in ("scheduled_at", "status", "decision_status", "proposal", "reason", "errors")}
            execution = event.get("execution", {})
            recent["execution"] = {k: deepcopy(execution[k]) for k in ("status", "plan", "error_type") if k in execution}
            for source, name in ((event.get("before_trader", event.get("before", {})), "account_before"),
                                 (event.get("after", {}), "account_after")):
                snapshot = source.get("snapshot", {})
                recent[name] = {k: snapshot[k] for k in ("quantity", "entry", "equity", "mark") if k in snapshot}
            self._update(last_cycle=slot, last_status=event["status"],
                         recent_decisions=(state["recent_decisions"] + [recent])[-24:])

    def run_forever(self, max_cycles=None, stop_event=None):
        if max_cycles is not None and (not isinstance(max_cycles, int) or max_cycles < 0):
            raise ValueError("max_cycles must be a nonnegative integer")
        stop_event = stop_event or threading.Event()
        self._stop_event = stop_event
        cycles = 0
        while not stop_event.is_set() and (max_cycles is None or cycles < max_cycles):
            try:
                if self._stop_requested():
                    break
                slot = int(self.clock() // DECISION_SECONDS) * DECISION_SECONDS
                if self.store.read("live-cycle-" + str(slot))[0] is None:
                    result = self.run(slot)
                    print(json.dumps({"event": "testnet_cycle", "slot": slot,
                                      "status": result["status"], "decision_status": result.get("decision_status"),
                                      "reason": result.get("reason"), "errors": result.get("errors", [])}), flush=True)
                    cycles += 1
                else:
                    self.maintain()
            except Exception as exc:
                try:
                    self.controller.maintain()
                except Exception:
                    pass
                try:
                    self._update(last_loop_error={"at": self.clock(), **_error("loop", exc)})
                except Exception:
                    pass
            if max_cycles is not None and cycles >= max_cycles:
                break
            stop_event.wait(PROTECTION_SECONDS)
        return summary(self.store)


def summary(store, *, limit=6):
    """Summarize observed Testnet facts, separating missing history and API reserves."""
    state = store.read("live-runtime")[0]
    if state is None:
        return {"status": "not_started", "network": "testnet"}
    cycles = sorted((r for _, r, _ in store.items("live-cycle-")), key=lambda r: r["scheduled_at"])
    accounts = sorted((r for _, r, _ in store.items("live-account-")), key=lambda r: r["at"])
    fills = [r["raw"] for _, r, _ in store.items("live-fill-")]
    funding = [r["raw"] for _, r, _ in store.items("live-funding-")]
    calls = [r for _, r, _ in store.items("llm-live-")]
    known = [r for r in calls if "input_tokens" in r and "output_tokens" in r]
    unknown = [r for r in calls if "input_tokens" not in r or "output_tokens" not in r]
    accounting_errors = []

    def total(rows, field, *, fee=False):
        value = dec(0)
        for row in rows:
            try:
                if fee and row.get("feeToken") != "USDC":
                    raise ValueError("fee token conversion unavailable")
                value += dec(row[field])
            except (KeyError, ValueError):
                accounting_errors.append(field)
                return None
        return str(value)

    closed = total(fills, "closedPnl")
    fees = total(fills, "fee", fee=True)
    funding_usdc = total([r.get("delta", r) for r in funding], "usdc")
    observed_net = str(dec(closed) - dec(fees) + dec(funding_usdc)) if all(x is not None for x in (closed, fees, funding_usdc)) else None
    api_known = str(sum((dec(r["estimated_usd"]) for r in known), dec(0)))
    api_unknown = str(sum((dec(r.get("estimated_usd", 0)) for r in unknown), dec(0)))
    health = state.get("audit_health", {})
    audit_ok = all(health.get(kind, {}).get("status") == "observed" for kind in ("fill", "funding"))
    latest = accounts[-1]["snapshot"] if accounts else None
    return {"status": state.get("last_status", "started"), "network": "testnet", "bot_id": state["bot_id"],
            "started_at": state["started_at"], "cycles": len(cycles),
            "complete_cycles": sum(r["status"] == "complete" for r in cycles),
            "account": latest, "account_observed_at": accounts[-1]["at"] if accounts else None,
            "sample_equity_change_usdc": str(dec(latest["equity"]) - dec(accounts[0]["snapshot"]["equity"])) if accounts else None,
            "equity_note": "Sampled account value; deposits, withdrawals and unrealized PnL are not separated.",
            "strategy": state["strategy"], "last_review": state["last_review"],
            "last_maintenance": state.get("last_maintenance"),
            "last_maintenance_error": state.get("last_maintenance_error"),
            "last_loop_error": state.get("last_loop_error"),
            "recent_decisions": [{k: r.get(k) for k in ("scheduled_at", "status", "decision_status", "reason", "proposal", "errors")} for r in cycles[-limit:]],
            "observed_fills": len(fills), "observed_funding_events": len(funding),
            "observed_closed_pnl_usdc": closed, "observed_fees_usdc": fees,
            "observed_funding_usdc": funding_usdc, "observed_realized_net_usdc": observed_net if audit_ok else None,
            "accounting_status": "observed_only" if audit_ok and not accounting_errors else "partial_or_unavailable",
            "accounting_errors": sorted(set(accounting_errors)), "history": health,
            "history_note": "Exchange history retention/completeness is not independently guaranteed; no profitability certification.",
            "api_calls": len(calls), "api_usage_known_calls": len(known), "api_unknown_calls": len(unknown),
            "api_estimated_known_usd": api_known, "api_unknown_reserved_usd": api_unknown,
            "api_input_tokens": sum(r.get("input_tokens", 0) for r in known),
            "api_output_tokens": sum(r.get("output_tokens", 0) for r in known),
            "news_status": "disabled", "profitability_evidence": False}
