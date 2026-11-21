"""Continuous Testnet orchestration; every exchange action stays in its controller.

The live ledger is separate from the shadow simulator and controller runtime.
An attempted ten-minute slot is never replayed, including after interruption.
"""
from copy import deepcopy
import hashlib
import json
import threading
import time
import uuid

from .config import dec
from .domain import parse_decision, plan
from .entries import SIDES, entry_contract, entry_summary, evaluate_entries, permits_increase
from .improvement import evaluate_improvement
from .initial_strategy import STRATEGY_RUN, initial_strategy
from .policy import validate_live_review
from .review_evidence import ENTRY_FIELDS, apply_review_evidence, build_review_evidence
from .store import Conflict


DECISION_SECONDS = 600
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

    def start_strategy_run(self, preflight):
        """Initialize this strategy once, keeping the existing reconciliation ledger."""
        state = self._state()
        if state.get("strategy_run") == STRATEGY_RUN:
            return state["strategy"], False
        if dec(preflight["quantity"]) or preflight["open_orders"]:
            raise LiveSafetyError("a new strategy run requires a flat account with no open orders")
        now = self.clock()
        next_slot = int(now // DECISION_SECONDS) * DECISION_SECONDS
        if now > next_slot + min(60, self.cfg.max_start_delay_seconds):
            next_slot += DECISION_SECONDS
        strategy = {**initial_strategy(self.cfg), "version": 1, "updated_at": now, "run": STRATEGY_RUN}
        trial = {"version": 1, "activated_slot": next_slot, "activated_at": now,
                 "previous_entries": None, "entries": deepcopy(strategy["entries"]),
                 "plan": deepcopy(strategy["improvement"]["next"]), "opening_position": "0"}
        self._update(strategy=strategy, strategy_version=1, strategy_run=STRATEGY_RUN,
                     strategy_started_at=now, strategy_seed_slot=next_slot, strategy_trial=trial,
                     last_review=None, last_review_slot=None, last_review_attempt=None, position_exit=None)
        return strategy, True

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
        updated_at = state["last_review"]
        if updated_at is None:
            updated_at = state.get("strategy_started_at")
        return (updated_at is not None and "entries" in (state.get("strategy") or {})
                and (state["last_review_attempt"] is None or state["last_review_attempt"] <= updated_at)
                and 0 <= self.clock() - updated_at <= self.cfg.strategy_ttl_seconds)

    def _position_exit(self, snapshot, hourly, slot):
        qty = dec(snapshot["quantity"])
        state = self._state()
        policy = state.get("position_exit")
        if not qty:
            if policy is not None:
                self._update(position_exit=None)
            return None
        side = "long" if qty > 0 else "short"
        if policy is None or policy["side"] != side:
            # An existing position from a v1 ledger has no persisted exit policy.
            runtime = self.store.read("runtime")[0] or {}
            policy = {"side": side, "rule": "hourly_close_vs_sma20",
                      "strategy_version": state["strategy_version"], "opened_slot": slot,
                      "stop_price": runtime.get("stop"), "last_trader_hour": None, "legacy": True}
            self._update(position_exit=policy)
        close, sma = dec(hourly["bars"][-1]["c"]), dec(hourly["sma20"])
        adverse = close < sma if side == "long" else close > sma
        if adverse and policy.get("exit_triggered_slot") is None:
            policy = {**policy, "exit_triggered_slot": slot}
            self._update(position_exit=policy)
        return {**policy, "triggered": policy.get("exit_triggered_slot") is not None,
                "hourly_close": str(close), "hourly_sma20": str(sma)}

    def _apply_decision(self, event, raw, market, snapshot, *, allow_increase):
        if self._stop_requested():
            event.update(status="skipped", decision_status="stop_requested")
            return event
        applied = self.controller.tick(raw, event["scheduled_at"], allow_increase=allow_increase,
                                       observed_mark=market["mark"], expected_quantity=snapshot["quantity"])
        event["execution"] = deepcopy(applied)
        event["decision_status"] = applied.get("status", "unknown")
        event["status"] = "complete" if applied.get("status") in {"checked", "complete"} else "partial"
        return event

    def _exit_position(self, event, market, snapshot):
        reason = "Complete the exit triggered by the position's hourly SMA20 condition."
        raw = {"intent": "set_target", "target_multiple": "0", "stop_price": None, "reason": reason}
        event.update(proposal=raw, reason=reason, decision_source="position_exit")
        return self._apply_decision(event, raw, market, snapshot, allow_increase=False)

    def _can_add(self, entry, checks, snapshot, market, account):
        if not checks["eligible"]:
            return False
        qty, equity, mark = (dec(snapshot[k]) for k in ("quantity", "equity", "mark"))
        desired = equity * abs(dec(entry["max_target_multiple"]))
        min_change = max(self.cfg.min_notional, equity * self.cfg.rebalance_threshold)
        if desired - abs(qty) * mark < min_change:
            return False
        if not account or account["stop"] is None:
            return True
        probe = {"intent": "set_target", "target_multiple": entry["max_target_multiple"],
                 "stop_price": account["stop"], "reason": "Estimate room at the current stop"}
        target = plan(account, mark, snapshot["step"], probe, self.cfg, observed_mark=market["mark"])
        return abs(dec(target["target"])) > abs(qty)

    def _review_evidence(self, state):
        since = state["last_review"]
        if since is None:
            since = state.get("strategy_started_at", state["started_at"])
        fills = [r for _, r, _ in self.store.items("live-fill-") if r["at"] >= since]
        funding = [r for _, r, _ in self.store.items("live-funding-") if r["at"] >= since]
        fills.sort(key=lambda r: r["at"])
        funding.sort(key=lambda r: r["at"])
        fill_fields = {"time", "coin", "side", "px", "sz", "closedPnl", "fee", "feeToken", "tid", "oid"}
        evidence = {"since": since, "until": self.clock(), "window_kind": "provider_event_time",
                "fills": [{k: v for k, v in r["raw"].items() if k in fill_fields} for r in fills[-20:]],
                "funding": [r["raw"] for r in funding[-20:]],
                "window_fill_count": len(fills), "window_funding_count": len(funding),
                "samples_truncated": len(fills) > 20 or len(funding) > 20,
                "note": "Samples are bounded and provider history may be incomplete."}
        if state.get("strategy_run") is None:
            cumulative = summary(self.store)
            evidence["cumulative_observed"] = {k: cumulative[k] for k in (
                    "observed_fills", "observed_funding_events", "observed_closed_pnl_usdc",
                    "observed_fees_usdc", "observed_funding_usdc", "observed_realized_net_usdc",
                    "accounting_status", "history", "api_estimated_known_usd", "api_unknown_reserved_usd")}
        return evidence

    def _improvement_feedback(self, state, examples, slot):
        trial = state["strategy_trial"]
        since, until = trial["activated_at"], self.clock()
        evidence = {"since": since, "until": until, "opening_position": trial["opening_position"], "history_complete": all(
            state["audit_health"].get(kind, {}).get("status") == "observed" for kind in ("fill", "funding"))}
        for kind, field in (("fill", "fills"), ("funding", "funding")):
            evidence[field] = [row["raw"] for _, row, _ in self.store.items(f"live-{kind}-")
                               if since <= row["at"] < until]
        return evaluate_improvement(trial, examples, evidence, until_slot=slot)

    def _apply_improvement(self, strategy, state, evidence_result, feedback, snapshot, slot, now):
        previous = state["strategy"]
        improvement = strategy["improvement"]
        idea = improvement["next"]
        side, field = idea["target"]["side"], idea["target"]["field"]
        admitted = [(side, field) for side in SIDES for field in ENTRY_FIELDS
                    if strategy["entries"][side][field] != previous["entries"][side][field]]
        applied = idea["action"] == "test_change" and admitted == [(side, field)]
        trial = state["strategy_trial"]
        if applied:
            result = {"status": "applied", "side": side, "field": field,
                      "from": previous["entries"][side][field], "to": strategy["entries"][side][field]}
            runtime = self.store.read("runtime")[0] or {}
            trial = {"version": strategy["version"], "activated_slot": slot, "activated_at": now,
                     "previous_entries": deepcopy(previous["entries"]), "entries": deepcopy(strategy["entries"]),
                     "plan": {**idea, "change": result},
                     "opening_position": runtime.get("last_quantity", snapshot["quantity"])}
        else:
            strategy["entries"] = deepcopy(previous["entries"])
            # Entry-related commentary continues to follow the rules actually in use.
            strategy["active_rules"] = deepcopy(previous["active_rules"])
            status = "deferred" if idea["action"] == "test_change" else "observe"
            result = {"status": status, "side": side, "field": field}
            strategy["change_reason"] = "Executable rules retained. " + (
                "The proposed single change did not pass the cited feasibility check."
                if status == "deferred" else "Continue observing the current rules.")
        strategy["improvement"] = {**improvement, "feedback": feedback, "result": result}
        evidence_result["activation"] = result
        return trial

    def run(self, scheduled_at):
        now = self.clock()
        if scheduled_at > now + 2 or int(scheduled_at) != scheduled_at or int(scheduled_at) % DECISION_SECONDS:
            raise ValueError("scheduled_at must be a nonfuture ten-minute boundary")
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
                 "news": [], "news_status": "disabled", "decision_status": "not_attempted",
                 "strategy_run": state.get("strategy_run")}
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
            snapshot = before["snapshot"]
            position_exit = self._position_exit(snapshot, features["1h"], slot)
            event["position_exit"] = position_exit
            if position_exit and position_exit["triggered"]:
                phase = "execution"
                return self._exit_position(event, market, snapshot)
            state = self._state()
            last_review_slot = state.get("last_review_slot")
            if last_review_slot is None:
                last_review_slot = state.get("strategy_seed_slot")
            due = last_review_slot is None or slot - last_review_slot >= REVIEW_SECONDS
            contract = entry_contract(self.cfg)
            if due:
                phase = "reviewer"
                self._update(last_review_attempt=self.clock(), last_review_slot=slot, directional_review_attempted=True)
                since = max(slot - REVIEW_SECONDS, state.get("strategy_seed_slot", 0))
                recent = [r for r in state["recent_decisions"] if since <= r["scheduled_at"] < slot
                          and r.get("strategy_run") == state.get("strategy_run")]
                examples = build_review_evidence(self.store, recent, self.cfg)
                inputs = {
                    "market": market, "features": features, "account": before["snapshot"],
                    "strategy": state["strategy"], "news": [], "news_status": "disabled",
                    "entry_contract": contract, "entry_summary": entry_summary(recent), "recent_decisions": recent,
                    "execution_evidence": self._review_evidence(state),
                    "rule_change_examples": examples,
                }
                feedback = None
                if state.get("strategy_trial"):
                    feedback = self._improvement_feedback(state, examples, slot)
                    inputs["improvement_feedback"] = feedback
                raw = self._policy_call("reviewer", slot, inputs, event)
                strategy = validate_live_review(raw, self.cfg, improvement_feedback=feedback)
                strategy, evidence_result = apply_review_evidence(strategy, state["strategy"], examples, self.cfg)
                event["rule_change_evidence"] = evidence_result
                strategy_version = state["strategy_version"] + 1
                reviewed_at = self.clock()
                strategy = {**strategy, "version": strategy_version, "updated_at": reviewed_at}
                updates = {"strategy": strategy, "strategy_version": strategy_version, "last_review": reviewed_at}
                if feedback is not None:
                    strategy["run"] = state["strategy_run"]
                    updates["strategy_trial"] = self._apply_improvement(
                        strategy, state, evidence_result, feedback, snapshot, slot, reviewed_at)
                state = self._update(**updates)
                event["review"] = deepcopy(strategy)
                # Review latency and protection may change both market and account.
                before = self.maintain()
                event["before_trader"] = deepcopy(before)
                if before.get("status") not in SAFE_MAINTENANCE or before.get("manual_pause"):
                    raise LiveSafetyError("maintenance not ready after review")
                market, features = self._observation()
                snapshot = before["snapshot"]
                position_exit = self._position_exit(snapshot, features["1h"], slot)
                event.update(market_before=deepcopy(market), features=features, position_exit=position_exit)
                if position_exit and position_exit["triggered"]:
                    phase = "execution"
                    return self._exit_position(event, market, snapshot)
            event["strategy"] = deepcopy(state["strategy"])
            if not self._fresh_strategy(state):
                event.update(status="blocked", decision_status="strategy_expired_or_missing")
                return event
            if self.clock() - scheduled_at > min(60, self.cfg.max_start_delay_seconds):
                event.update(status="skipped", decision_status="late_after_review")
                return event
            phase = "trader"
            runtime = self.store.read("runtime")[0] or {}
            entries = state["strategy"]["entries"]
            checks = evaluate_entries(entries, features, market)
            event.update(market_before=deepcopy(market), features=features, entry_checks=checks)
            event["review_risk_state"] = {k: runtime["account"][k] for k in (
                "stop", "manual_pause", "dd_stopped_at", "partial") if k in runtime.get("account", {})}
            hour = features["1h"]["bars"][-1]["t"]
            qty = dec(snapshot["quantity"])
            if not qty and not any(checks[side]["eligible"] for side in entries):
                event.update(status="complete", decision_status="flat_no_entry", decision_source="entry_filter",
                             reason="Neither side satisfies the entry conditions.")
                return event
            if position_exit and position_exit["last_trader_hour"] == hour:
                side = position_exit["side"]
                if not self._can_add(entries[side], checks[side], snapshot, market, runtime.get("account")):
                    event.update(status="complete", decision_status="position_unchanged", decision_source="entry_filter",
                                 reason="The hourly candle is unchanged and there is no eligible position increase.")
                    return event
            inputs = {"market": market, "features": features,
                      "account": deepcopy(runtime.get("account", snapshot)),
                      "account_snapshot": snapshot, "equity": snapshot["equity"], "position_exit": position_exit,
                      "strategy": state["strategy"],
                      "recent_decisions": [r for r in state["recent_decisions"]
                                           if r.get("strategy_run") == state.get("strategy_run")][-6:],
                      "entry_contract": contract, "entry_checks": checks,
                      "risk": {"max_exposure": str(self.cfg.max_exposure), "stop_risk": str(self.cfg.stop_risk)}}
            raw = self._policy_call("trader", slot, inputs, event)
            decision = parse_decision(raw)
            event.update(proposal=deepcopy(raw), reason=raw["reason"], decision_source="trader")
            entry_allowed = permits_increase(decision, entries, checks) and self._fresh_strategy(self._state())
            target = decision["target_multiple"]
            if qty and target is not None and qty * target < 0:
                raw = {**raw, "target_multiple": "0", "stop_price": None}
                entry_allowed = False
            if position_exit:
                policy = self._state()["position_exit"]
                current_stop = (runtime.get("account") or {}).get("stop")
                limits = [dec(price) for price in (policy["stop_price"], current_stop) if price is not None]
                if limits and raw["stop_price"] is not None:
                    protected = max(limits) if qty > 0 else min(limits)
                    proposed = dec(raw["stop_price"])
                    if (qty > 0 and proposed < protected) or (qty < 0 and proposed > protected):
                        raw = {**raw, "stop_price": str(protected)}
                self._update(position_exit={**policy, "last_trader_hour": hour})
            elif target and entry_allowed and decision["stop_price"] is not None:
                # Persist before submission so a fill followed by a crash retains its opening strategy.
                self._update(position_exit={"side": "long" if target > 0 else "short",
                                            "rule": "hourly_close_vs_sma20", "strategy_version": state["strategy_version"],
                                            "opened_slot": slot, "stop_price": str(decision["stop_price"]),
                                            "last_trader_hour": hour})
            if raw != event["proposal"]:
                event["execution_decision"] = deepcopy(raw)
            event["entry_allows_increase"] = entry_allowed
            phase = "execution"
            return self._apply_decision(event, raw, market, snapshot, allow_increase=entry_allowed)
        except Exception as exc:
            event["errors"].append(_error(phase, exc))
            event["status"] = "error"
            event["decision_status"] = phase + "_failed"
            return event
        finally:
            try:
                event["after"] = self.maintain()
                after = event["after"]
                if (after.get("status") in SAFE_MAINTENANCE and not after.get("manual_pause")
                        and dec(after["snapshot"]["quantity"]) == 0 and self._state().get("position_exit") is not None):
                    self._update(position_exit=None)
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
            recent = {k: deepcopy(event.get(k)) for k in (
                "scheduled_at", "status", "decision_status", "proposal", "reason", "errors", "entry_checks",
                "entry_allows_increase", "decision_source", "position_exit", "strategy_run")}
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
            "entry_summary_last_24_cycles": entry_summary(cycles[-24:]),
            "position_exit": state.get("position_exit") if latest and dec(latest["quantity"]) else None,
            "recent_decisions": [{k: r.get(k) for k in ("scheduled_at", "status", "decision_status", "reason", "proposal",
                                                         "entry_checks", "entry_allows_increase", "decision_source",
                                                         "position_exit", "rule_change_evidence", "errors")} for r in cycles[-limit:]],
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
