"""Decimal accounting and the agreed dynamic-equity risk contract."""
from decimal import Decimal, ROUND_DOWN
from .config import dec

ZERO = Decimal(0)
MULTIPLES = {Decimal(n) / 2 for n in range(-6, 7)}


def account(initial):
    return {"cash": str(initial), "quantity": "0", "entry": "0", "stop": None,
            "fees": "0", "funding": "0", "realized": "0", "version": 0,
            "opened_at": None, "peak": str(initial), "segment_peak": str(initial),
            "max_dd": "0", "dd_stopped_at": None, "stop_count": 0, "fills": 0,
            "manual_pause": False, "partial": False, "gap_since": None}


def equity(a, mark):
    return dec(a["cash"]) + dec(a["quantity"]) * (dec(mark) - dec(a["entry"]))


def track_risk(a, mark, now, cfg, *, recover=True):
    eq = equity(a, mark)
    peak = max(dec(a["peak"]), eq)
    segment = max(dec(a["segment_peak"]), eq)
    dd = max(ZERO, (peak - eq) / peak)
    a.update(peak=str(peak), segment_peak=str(segment), max_dd=str(max(dec(a["max_dd"]), dd)))
    if a["dd_stopped_at"] is None and (segment - eq) / segment >= cfg.drawdown_limit:
        a["dd_stopped_at"] = now
        a["stop_count"] += 1
    if (recover and a["dd_stopped_at"] is not None and now - a["dd_stopped_at"] >= cfg.cooldown_seconds
            and dec(a["quantity"]) == 0 and not a["manual_pause"] and not a["partial"] and eq > 0):
        a["segment_peak"] = str(eq)
        a["dd_stopped_at"] = None


def floor_qty(value, step):
    step = dec(step)
    if step <= 0:
        raise ValueError("invalid quantity step")
    return (abs(value) / step).to_integral_value(rounding=ROUND_DOWN) * step


def capacity(a, mark, stop, direction, step, cfg):
    """Cap post-fee equity conservatively, including full turnover and SL exit costs."""
    mark, stop = dec(mark), dec(stop)
    if mark <= 0 or stop <= 0 or direction not in {-1, 1} or direction * (mark - stop) <= 0:
        raise ValueError("stop must protect the proposed side")
    eq = max(ZERO, equity(a, mark))
    cost_rate = cfg.fee_rate + cfg.slippage_bps / 10000
    # Reserve cost of closing the entire current position, plus opening the target.
    available = max(ZERO, eq - abs(dec(a["quantity"])) * mark * cost_rate)
    exposure_cap = cfg.max_exposure * available / (mark * (1 + cfg.max_exposure * cost_rate))
    stop_cap = cfg.stop_risk * available / (abs(mark - stop) + mark * cost_rate * (1 + cfg.stop_risk))
    return floor_qty(min(exposure_cap, stop_cap), step)


def parse_decision(raw):
    if not isinstance(raw, dict) or set(raw) != {"intent", "target_multiple", "stop_price", "reason"}:
        raise ValueError("unexpected decision fields")
    if raw["intent"] not in {"hold", "set_target"} or not isinstance(raw["reason"], str) or len(raw["reason"]) > 2000:
        raise ValueError("invalid decision")
    multiple = None if raw["target_multiple"] is None else dec(raw["target_multiple"])
    stop = None if raw["stop_price"] is None else dec(raw["stop_price"])
    if raw["intent"] == "hold" and multiple is not None:
        raise ValueError("hold cannot set a target")
    if raw["intent"] == "set_target" and multiple not in MULTIPLES:
        raise ValueError("target must be a configured half multiple")
    if stop is not None and stop <= 0:
        raise ValueError("invalid stop")
    if multiple == 0 and stop is not None:
        raise ValueError("flat target cannot set stop")
    return {**raw, "target_multiple": multiple, "stop_price": stop}


def plan(a, mark, step, raw, cfg, *, allow_increase=True, observed_mark=None):
    d = parse_decision(raw)
    mark = dec(mark)
    if mark <= 0:
        raise ValueError("invalid mark")
    current = dec(a["quantity"])
    desired = current if d["intent"] == "hold" else floor_qty(max(ZERO, equity(a, mark)) * abs(d["target_multiple"]) / mark, step).copy_sign(d["target_multiple"])
    if desired == 0:
        return {"target": "0", "stop": None, "reason": "close" if current else "flat"}
    direction = 1 if desired > 0 else -1
    stop = d["stop_price"]
    if stop is None and current * desired > 0:
        stop = a["stop"]
    if stop is None:
        raise ValueError("nonzero target requires stop")
    allowed = capacity(a, mark, stop, direction, step, cfg)
    desired = min(abs(desired), allowed) * direction
    increase = abs(desired) > abs(current) or current * desired < 0
    blocked = not allow_increase or a["manual_pause"] or a["dd_stopped_at"] is not None or a["partial"]
    if observed_mark is not None and abs(mark / dec(observed_mark) - 1) >= cfg.price_drift_limit:
        blocked = True
    if increase and blocked:
        # A reversal may close the existing side but must not reopen the other side.
        return {"target": "0" if current * desired < 0 else str(current), "stop": None if current * desired < 0 else a["stop"], "reason": "increase_blocked"}
    safety_reduction = abs(current) > allowed and current * direction > 0
    delta_notional = abs(desired - current) * mark
    if desired and not safety_reduction and current * desired >= 0:
        if delta_notional < equity(a, mark) * cfg.rebalance_threshold or (increase and delta_notional < cfg.min_notional):
            desired = current
    if not current and abs(desired) * mark < cfg.min_notional:
        desired = ZERO
    return {"target": str(desired), "stop": str(stop) if desired else None,
            "reason": "risk_resize" if safety_reduction else "target"}


def book_price(levels, quantity, buy, slippage_bps):
    quantity = abs(dec(quantity))
    remaining, total = quantity, ZERO
    for level in levels:
        price, size = dec(level["px"]), dec(level["sz"])
        if price <= 0 or size < 0:
            raise ValueError("invalid book")
        take = min(remaining, size)
        total += take * price
        remaining -= take
        if remaining == 0:
            break
    if remaining or quantity == 0:
        raise ValueError("insufficient book depth")
    return total / quantity * (1 + (1 if buy else -1) * slippage_bps / 10000)


def fill(a, delta, price, now, cfg, reason):
    delta, price = dec(delta), dec(price)
    old, entry = dec(a["quantity"]), dec(a["entry"])
    if reason != "stop_ohlc_conservative":
        remember_transition(a, now)
    if delta == 0 or price <= 0:
        raise ValueError("invalid fill")
    closed = min(abs(old), abs(delta)) if old * delta < 0 else ZERO
    pnl = closed * (price - entry) * (1 if old > 0 else -1)
    fee = abs(delta) * price * cfg.fee_rate
    new = old + delta
    if old * delta >= 0:
        a["entry"] = str((abs(old) * entry + abs(delta) * price) / abs(new))
    elif old * new < 0:
        a["entry"] = str(price)
    if not new:
        a.update(entry="0", stop=None, opened_at=None)
    elif not old or old * new < 0:
        a["opened_at"] = now
    a.update(quantity=str(new), cash=str(dec(a["cash"]) + pnl - fee),
             realized=str(dec(a["realized"]) + pnl), fees=str(dec(a["fees"]) + fee),
             version=a["version"] + 1, fills=a["fills"] + 1)
    return {"kind": "fill", "at": now, "quantity": str(delta), "price": str(price), "fee": str(fee), "pnl": str(pnl), "reason": reason}


def execute(a, proposed, market, now, cfg):
    target, current = dec(proposed["target"]), dec(a["quantity"])
    events = []
    if current * target < 0:
        events += execute(a, {"target": "0", "stop": None, "reason": "reversal_close"}, market, now, cfg)
        target = min(abs(target), capacity(a, market["mark"], proposed["stop"], 1 if target > 0 else -1, market["step"], cfg)).copy_sign(target)
        current = ZERO
    delta = target - current
    if delta:
        px = book_price(market["asks"] if delta > 0 else market["bids"], delta, delta > 0, cfg.slippage_bps)
        events.append(fill(a, delta, px, now, cfg, proposed["reason"]))
    new_stop = proposed["stop"] if dec(a["quantity"]) else None
    if a["stop"] != new_stop:
        remember_transition(a, now)
        a["stop"] = new_stop
        a["version"] += 1
        events.append({"kind": "stop", "at": now, "price": new_stop})
    return events


def remember_transition(a, now):
    """Retain pre-edit protection until the containing minute can be replayed."""
    row = {"at": now, "quantity": a["quantity"], "stop": a["stop"]}
    pending = a.setdefault("minute_transitions", [])
    if row not in pending:
        pending.append(row)


def enforce(a, market, now, cfg):
    current = dec(a["quantity"])
    if not current:
        return []
    mark = dec(market["mark"])
    stop = a["stop"]
    if stop is None or current * (mark - dec(stop)) <= 0 or equity(a, mark) <= 0:
        proposed = {"target": "0", "stop": None, "reason": "unprotected_or_triggered"}
    else:
        allowed = capacity(a, mark, stop, 1 if current > 0 else -1, market["step"], cfg)
        proposed = {"target": str(min(abs(current), allowed).copy_sign(current)), "stop": stop, "reason": "risk_resize"}
    return execute(a, proposed, market, now, cfg)


def advance(a, bars, funding, since, until, cfg, *, protected=True):
    """Replay complete minute bars; ambiguous partial entry bars are explicitly tagged."""
    events = []
    expected = set(range(int(since // 60) * 60, int(until // 60) * 60, 60))
    by_time = {int(b["t"]) // 1000: b for b in bars}
    if expected - by_time.keys():
        a["partial"] = True
        a["gap_since"] = a["gap_since"] or since
        return [{"kind": "gap", "since": since, "until": until}]
    for start in sorted(expected):
        bar = by_time[start]
        low, high, opening = dec(bar["l"]), dec(bar["h"]), dec(bar["o"])
        qty = dec(a["quantity"])
        opened = a.get("opened_at")
        if opened is not None and start + 60 <= opened:
            continue
        # Public funding history has no settlement mark: adverse bar extremum proxy.
        charges = []
        for f in funding:
            at = int(f["time"]) / 1000
            if max(since, start, opened or 0) < at <= start + 60:
                rate = dec(f["fundingRate"])
                amount = qty * rate * (high if qty * rate > 0 else low)
                charges.append((at, amount))
        stop = dec(a["stop"]) if a["stop"] is not None else None
        transitions = [t for t in a.get("minute_transitions", []) if start < t["at"] < start + 60]
        old_hits = [t for t in transitions if t["stop"] is not None and dec(t["quantity"]) != 0
                    and (low <= dec(t["stop"]) if dec(t["quantity"]) > 0 else high >= dec(t["stop"]))]
        if protected and old_hits:
            # OHLC cannot decide whether an old stop preceded the intra-minute edit.
            # Keep the ambiguity visible; use a touched stop adverse to the surviving side.
            a["partial"] = True
            events.append({"kind": "ambiguous_stop_edit", "at": start + 60, "transitions": old_hits})
            same_side = [dec(t["stop"]) for t in old_hits if dec(t["quantity"]) * qty > 0]
            if same_side:
                candidate = min(same_side) if qty > 0 else max(same_side)
                if stop is None or not (low <= stop if qty > 0 else high >= stop):
                    stop = candidate
        hit = protected and qty != 0 and stop is not None and (low <= stop if qty > 0 else high >= stop)
        if hit and transitions and not old_hits:
            a["partial"] = True
            events.append({"kind": "ambiguous_stop_edit", "at": start + 60, "transitions": transitions})
        for at, amount in charges:
            if transitions:
                a["partial"] = True
                events.append({"kind": "ambiguous_funding_transition", "at": at})
            # Ambiguous collision: charge debits before SL; never credit after a possible SL.
            if hit and amount < 0:
                amount = ZERO
            a["cash"] = str(dec(a["cash"]) - amount)
            a["funding"] = str(dec(a["funding"]) + amount)
            events.append({"kind": "funding", "at": at, "amount": str(amount), "approximation": "adverse_bar_mark_and_order"})
        if hit:
            trigger = min(stop, opening) if qty > 0 else max(stop, opening)
            px = trigger * (1 + (-1 if qty > 0 else 1) * cfg.slippage_bps / 10000)
            events.append(fill(a, -qty, px, start + 60, cfg, "stop_ohlc_conservative"))
            if start < max(since, opened or 0):
                events[-1]["ambiguous_partial_bar"] = True
                a["partial"] = True
        if protected:
            track_risk(a, dec(bar["c"]), start + 60, cfg, recover=False)
        else:
            eq = equity(a, dec(bar["c"]))
            a["peak"] = str(max(dec(a["peak"]), eq))
            a["max_dd"] = str(max(dec(a["max_dd"]), (dec(a["peak"]) - eq) / dec(a["peak"])))
    a["minute_transitions"] = [t for t in a.get("minute_transitions", []) if t["at"] >= until]
    return events
