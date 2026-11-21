"""Replay candidate entry rules on the observations that preceded a live review."""
from collections import defaultdict
from itertools import zip_longest

from .config import dec
from .domain import account, plan
from .entries import SIDES, TRENDS, TRIGGERS, evaluate_entries, multiples, validate_entries
from .testnet import coverage, ioc_limit_price


ENTRY_FIELDS = ("enabled", "trend", "trigger", "max_spread_bps", "max_target_multiple")


def _signal_flags(features, market):
    """Use the same predicates as the live entry path without a strategy cap."""
    flags = {side: {"trends": [], "triggers": []} for side in SIDES}
    for trend, trigger in zip_longest(TRENDS, TRIGGERS):
        rule = {"enabled": True, "trend": trend or TRENDS[0], "trigger": trigger or TRIGGERS[0], "max_spread_bps": 1}
        checks = evaluate_entries(dict.fromkeys(SIDES, rule), features, market)
        for side in SIDES:
            if trend and "trend" not in checks[side]["failed"]:
                flags[side]["trends"].append(trend)
            if trigger and "trigger" not in checks[side]["failed"]:
                flags[side]["triggers"].append(trigger)
    return checks["spread_bps"], flags


def _entry_stop(cycle, before, features, mark, sign):
    proposal = cycle.get("proposal") or {}
    multiple = dec(proposal.get("target_multiple") or 0)
    if multiple * sign > 0 and proposal.get("stop_price") is not None:
        return dec(proposal["stop_price"]), "historical_proposal"
    if dec(before["quantity"]) * sign > 0:
        state = cycle.get("review_risk_state") or {}
        if state.get("stop") is not None:
            return dec(state["stop"]), "existing_stop"
        _, orders = coverage(before)
        if orders:
            price = min(sign * dec(o["triggerPx"]) for o in orders) * sign
            return price, "existing_stop"
        return None, "missing_existing_stop"
    # Flat historic hold decisions have no proposed stop. This common probe is
    # used to compare entry rules on the same inputs, with no implied live order.
    atr = dec(features["5m"]["atr14"])
    return mark - sign * 2 * atr, "hypothetical_2x_5m_atr"


def _order_deltas(cycle, before, cfg, side, features, market):
    sign = 1 if side == "long" else -1
    qty, mark, eq = (dec(before[k]) for k in ("quantity", "mark", "equity"))
    if qty * sign < 0:
        return {"probe": "opposite_position", "deltas": {}}
    if "available_collateral" not in before:
        return {"probe": "unknown_collateral", "deltas": {}}
    stop, probe = _entry_stop(cycle, before, features, mark, sign)
    if stop is None or stop <= 0 or sign * (mark - stop) <= 0:
        return {"probe": "unavailable_protective_stop", "deltas": {}}
    risk_state = cycle.get("review_risk_state") or {}
    portfolio = account(eq)
    portfolio.update(quantity=str(qty), entry=before["entry"], stop=str(stop),
                     manual_pause=bool(risk_state.get("manual_pause")),
                     dd_stopped_at=risk_state.get("dd_stopped_at"), partial=bool(risk_state.get("partial")))
    portfolio["cash"] = str(eq - qty * (mark - dec(before["entry"])))
    limit = dec(ioc_limit_price(mark, sign > 0))
    levels = market["asks"] if sign > 0 else market["bids"]
    depth = sum((dec(l["sz"]) for l in levels if (limit - dec(l["px"])) * sign >= 0), dec(0))
    margin_price = max(mark, limit, dec(levels[0]["px"]))
    collateral = dec(before["available_collateral"])
    deltas = {}
    for multiple in multiples(cfg, side):
        raw = {"intent": "set_target", "target_multiple": multiple, "stop_price": str(stop), "reason": "review replay"}
        result = plan(portfolio, mark, before["step"], raw, cfg, observed_mark=market["mark"])
        delta = sign * (dec(result["target"]) - qty)
        # The local Testnet account configures 3x leverage. Conservatively
        # estimate incremental margin and fee, and require full displayed depth.
        if 0 < delta <= depth and delta * margin_price * (dec(1) / 3 + cfg.fee_rate) <= collateral:
            deltas[multiple] = str(delta * sign)
    return {"probe": probe, "stop": str(stop), "ioc_depth_btc": str(depth), "deltas": deltas}


def build_review_evidence(store, recent, cfg):
    """Return compact evidence for the reviewer and the post-response verifier."""
    examples = []
    for row in recent:
        slot = row["scheduled_at"]
        cycle, _ = store.read(f"live-cycle-{slot}")
        if not cycle or not cycle.get("entry_checks"):
            continue
        try:
            before = (cycle.get("before_trader") or cycle["before"])["snapshot"]
            market, features = cycle["market_before"], cycle["features"]
            spread, flags = _signal_flags(features, market)
            for side in SIDES:
                flags[side].update(_order_deltas(cycle, before, cfg, side, features, market))
            examples.append({"scheduled_at": slot, "quantity": before["quantity"], "equity": before["equity"],
                             "spread_bps": spread, **flags})
        except (KeyError, ValueError, IndexError, TypeError, ZeroDivisionError):
            # A legacy or incomplete observation is not evidence of feasibility.
            continue
    return examples


def _feasible_sizes(rule, side, row):
    checks = row[side]
    if (not rule["enabled"] or rule["trend"] not in checks["trends"] or rule["trigger"] not in checks["triggers"]
            or dec(row["spread_bps"]) > dec(rule["max_spread_bps"])):
        return set()
    cap = abs(dec(rule["max_target_multiple"]))
    return {delta for multiple, delta in checks["deltas"].items() if abs(dec(multiple)) <= cap}


def _supported_fields(old, candidate, fields, side, claimed):
    supported = defaultdict(list)
    for row in claimed:
        new_sizes = _feasible_sizes(candidate, side, row)
        if _feasible_sizes(old, side, row) == new_sizes:
            continue
        for field in fields:
            if _feasible_sizes({**candidate, field: old[field]}, side, row) != new_sizes:
                supported[field].append(row["scheduled_at"])
    return dict(supported)


def apply_review_evidence(strategy, previous, examples, cfg):
    """Keep prior machine fields unless cited observations support a change."""
    refs = strategy.get("rule_change_evidence_slots", {})
    if not previous or not previous.get("entries"):
        return strategy, {"status": "initial_strategy", "examples": len(examples)}

    by_slot = {row["scheduled_at"]: row for row in examples}
    updated = {}
    report = {}
    for side in SIDES:
        old, new = previous["entries"][side], strategy["entries"][side]
        fields = [k for k in ENTRY_FIELDS if old[k] != new[k]]
        remaining = fields[:]
        claimed = [by_slot[t] for t in dict.fromkeys(refs.get(side, [])) if t in by_slot]
        merged = new.copy()
        # Dropping an unsupported field can affect the proof for another one.
        while True:
            accepted = _supported_fields(old, merged, remaining, side, claimed)
            unsupported = [field for field in remaining if field not in accepted]
            if not unsupported:
                break
            merged.update({field: old[field] for field in unsupported})
            remaining = [field for field in remaining if field in accepted]
        retained = [field for field in fields if field not in accepted]
        # Avoid narrative describing a rule change that failed verification.
        # A partial acceptance retains only the demonstrated machine fields.
        if retained:
            merged.update({k: old[k] for k in ("rationale", "invalidation", "reconsider_when")})
        updated[side] = merged
        report[side] = {"accepted": accepted, "retained": retained}
    validate_entries(updated, cfg)
    result = {**strategy, "entries": updated}
    if any(report[side]["retained"] for side in SIDES):
        # The proposal and its discussion remain in the original reviewer response.
        details = [f"{side}: kept {', '.join(report[side]['retained'])}"
                   for side in SIDES if report[side]["retained"]]
        approved = [f"{side}: updated {', '.join(report[side]['accepted'])}"
                    for side in SIDES if report[side]["accepted"]]
        result["change_reason"] = ". ".join(["Replay review", *approved, *details])
    return result, {"status": "compared", "examples": len(examples), **report}
