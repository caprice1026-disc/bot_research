"""Measure observations made after an adopted entry rule entered service."""
from .config import dec
from .entries import SIDES
from .review_evidence import ENTRY_FIELDS, _feasible_sizes


def _provider_records(records, since, until):
    rows = []
    for record in records:
        raw = record.get("raw", record)
        at = record.get("at")
        if at is None:
            at = dec(raw["time"]) / 1000
        if dec(since) <= dec(at) < dec(until):
            rows.append((dec(at), raw))
    return [raw for _, raw in sorted(rows, key=lambda row: (row[0], int(row[1].get("tid") or 0)))]


def _episode_counts(fills, opening_position):
    closing_fills = closed = completed = 0
    position = dec(opening_position) if opening_position is not None else None
    started_in_window = False
    continuous = True
    for fill in fills:
        if fill.get("side") not in {"A", "B"} or fill.get("startPosition") is None:
            continuous = False
            continue
        try:
            start, size = dec(fill["startPosition"]), dec(fill["sz"])
            if size <= 0:
                raise ValueError("fill quantity must be positive")
        except (ValueError, KeyError):
            continuous = False
            continue
        if position is not None and start != position:
            continuous = False
        signed = size if fill["side"] == "B" else -size
        position = start + signed
        if start * signed < 0:
            closing_fills += 1
        ends_episode = bool(start) and start * position <= 0
        if ends_episode:
            closed += 1
            completed += started_in_window
        if not start or ends_episode:
            started_in_window = bool(position)
    return {"closing_fills": closing_fills if continuous else None,
            "closed_episodes": closed if continuous else None,
            "completed_episodes": completed if continuous else None,
            "position_history_continuous": continuous}


def _total(records, key):
    try:
        return sum((dec(row[key]) for row in records), dec(0))
    except (ValueError, KeyError):
        return None


def _observed(evidence, since, until):
    fills = _provider_records(evidence["fills"], since, until)
    funding = _provider_records(evidence["funding"], since, until)
    counts = _episode_counts(fills, evidence.get("opening_position"))
    pnl = _total(fills, "closedPnl")
    funding_usdc = _total((row.get("delta", row) for row in funding), "usdc")
    fees = _total(fills, "fee") if all(fill.get("feeToken") == "USDC" for fill in fills) else None
    observed_history = evidence["history_complete"] and counts["position_history_continuous"]
    known_amounts = all(value is not None for value in (pnl, fees, funding_usdc))
    net = pnl - fees + funding_usdc if observed_history and known_amounts else None
    assessed = net is not None and counts["completed_episodes"] >= 2
    return {"status": "observed" if assessed else "insufficient",
            "history_observed": observed_history, "fills": len(fills), "funding_events": len(funding),
            "realized_pnl_usdc": None if pnl is None else str(pnl),
            "fees_usdc": None if fees is None else str(fees),
            "funding_usdc": None if funding_usdc is None else str(funding_usdc),
            "net_cash_flow_usdc": None if net is None else str(net),
            **counts}


def _feasibility(trial, examples):
    prior = trial["previous_entries"]
    active = trial["entries"]
    changed = False
    counts = {}
    for side in SIDES:
        new = [_feasible_sizes(active[side], side, row) for row in examples]
        counts[side] = {"active_feasible": sum(bool(sizes) for sizes in new)}
        if prior is None:
            continue
        old = [_feasible_sizes(prior[side], side, row) for row in examples]
        pairs = list(zip(old, new))
        counts[side].update(
            prior_feasible=sum(bool(sizes) for sizes in old),
            added=sum(not a and bool(b) for a, b in pairs),
            removed=sum(bool(a) and not b for a, b in pairs),
            resized=sum(bool(a and b) and a != b for a, b in pairs),
        )
        changed |= any(a != b for a, b in pairs)
    status = "observed" if changed else "baseline" if prior is None else "insufficient"
    return {"status": status, "examples": len(examples), **counts}


def evaluate_improvement(trial, examples, execution_evidence, *, until_slot):
    """Return replay and execution observations, bounded to the actual trial window."""
    if not trial:
        return {"status": "no_trial"}
    past_rows = [row for row in examples if trial["activated_slot"] <= row["scheduled_at"] < until_slot]
    since = max(trial["activated_at"], execution_evidence["since"])
    until = execution_evidence["until"]
    execution = _observed(execution_evidence, since, until)
    replay = _feasibility(trial, past_rows)
    prior = trial["previous_entries"]
    changes = {} if prior is None else {
        side: {name: {"before": prior[side][name], "after": trial["entries"][side][name]}
               for name in ENTRY_FIELDS if prior[side][name] != trial["entries"][side][name]}
        for side in SIDES
    }
    assessed = execution["status"] == "observed" or replay["status"] == "observed"
    status = "no_trial" if prior is None else "observed" if assessed else "insufficient"
    return {"status": status, "trial_version": trial["version"],
            "plan": trial["plan"], "applied_changes": changes,
            "window": {"execution_since": since, "execution_until": until,
                       "first_feasibility_slot": min((row["scheduled_at"] for row in past_rows), default=None),
                       "feasibility_until_slot": until_slot},
            "observed": execution, "feasibility": replay,
            "limit": ("Replay compares displayed order feasibility on the same snapshots. "
                      "Cash flow describes the account during the window. It excludes unrealized PnL and model costs. "
                      "It can include positions carried into the trial. A minimum of two opened-and-closed episodes "
                      "is required for descriptive execution assessment. Neither comparison establishes causation.")}
