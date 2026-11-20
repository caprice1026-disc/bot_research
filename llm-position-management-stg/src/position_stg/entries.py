"""Executable long and short entry rules for the continuous Testnet strategy."""
from collections import Counter

from .config import dec
from .domain import MULTIPLES


SIDES = ("long", "short")
TRENDS = ("any", "hourly_close_vs_sma20", "hourly_sma20_vs_sma50")
TRIGGERS = ("sma20_retest", "sma20_cross", "six_bar_breakout", "close_vs_sma20")


def multiples(cfg, side):
    sign = 1 if side == "long" else -1
    return [f"{m:.1f}" for m in sorted(MULTIPLES, key=abs) if 0 < sign * m <= cfg.max_exposure]


def entry_contract(cfg):
    return {
        "target_unit": "signed target BTC notional / current account equity, before risk clipping",
        "multiples": {side: multiples(cfg, side) for side in SIDES},
        "min_notional_usdc": str(cfg.min_notional), "max_stop_loss_fraction": str(cfg.stop_risk),
        "rebalance_equity_fraction": str(cfg.rebalance_threshold),
        "fee_rate": str(cfg.fee_rate), "slippage_bps": str(cfg.slippage_bps),
        "execution": "marketable IOC limit; protective reduce-only stop; passive orders unavailable",
    }


def side_schema(cfg, side):
    fields = {
        "enabled": {"type": "boolean"},
        "trend": {"type": "string", "enum": list(TRENDS)},
        "trigger": {"type": "string", "enum": list(TRIGGERS)},
        "max_spread_bps": {"type": "number"},
        "max_target_multiple": {"type": "string", "enum": multiples(cfg, side) or ["0.0"]},
        "rationale": {"type": "string"}, "invalidation": {"type": "string"},
        "reconsider_when": {"type": "string"},
    }
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def validate_entries(entries, cfg):
    if not isinstance(entries, dict) or set(entries) != set(SIDES):
        raise ValueError("both entry plans are required")
    for side, rule in entries.items():
        if not isinstance(rule, dict) or set(rule) != set(side_schema(cfg, side)["required"]):
            raise ValueError("invalid entry fields")
        if (type(rule["enabled"]) is not bool or rule["trend"] not in TRENDS or rule["trigger"] not in TRIGGERS
                or rule["max_target_multiple"] not in (multiples(cfg, side) or ["0.0"])
                or (rule["enabled"] and rule["max_target_multiple"] == "0.0")
                or type(rule["max_spread_bps"]) not in (int, float) or dec(rule["max_spread_bps"]) <= 0):
            raise ValueError("invalid entry parameters")
        for key in ("rationale", "invalidation", "reconsider_when"):
            if not isinstance(rule[key], str) or not 0 < len(rule[key].strip()) <= 500:
                raise ValueError("missing or oversized entry explanation")


def evaluate_entries(entries, features, market):
    """All levels use this observation only. Breakouts exclude the signal candle."""
    fast, hourly = features["5m"], features["1h"]
    bars = fast["bars"]
    bar = bars[-1]
    close, sma = dec(bar["c"]), dec(fast["sma20"])
    prev_close = dec(bars[-2]["c"])
    prev_sma = sum(dec(b["c"]) for b in bars[-21:-1]) / 20
    bid, ask = dec(market["bids"][0]["px"]), dec(market["asks"][0]["px"])
    spread = 20000 * (ask - bid) / (ask + bid)
    results = {}
    for side, sign in (("long", 1), ("short", -1)):
        rule = entries[side]
        extreme = "l" if side == "long" else "h"
        breakout = (max(dec(b["h"]) for b in bars[-7:-1]) if side == "long"
                    else min(dec(b["l"]) for b in bars[-7:-1]))
        crossed = sign * (close - sma) > 0
        triggers = {
            "sma20_cross": crossed and sign * (prev_close - prev_sma) <= 0,
            "sma20_retest": crossed and sign * (prev_close - prev_sma) > 0 and sign * (dec(bar[extreme]) - sma) <= 0,
            "six_bar_breakout": sign * (close - breakout) > 0,
            "close_vs_sma20": crossed,
        }
        trends = {
            "any": True,
            "hourly_close_vs_sma20": sign * (dec(hourly["bars"][-1]["c"]) - dec(hourly["sma20"])) > 0,
            "hourly_sma20_vs_sma50": sign * (dec(hourly["sma20"]) - dec(hourly["sma50"])) > 0,
        }
        checks = {"enabled": rule["enabled"], "trend": trends[rule["trend"]],
                  "trigger": triggers[rule["trigger"]], "spread": spread <= dec(rule["max_spread_bps"])}
        results[side] = {"signal_met": checks["trend"] and checks["trigger"],
                         "eligible": all(checks.values()), "failed": [k for k, met in checks.items() if not met]}
    return {"spread_bps": str(spread), **results}


def permits_increase(decision, entries, checks):
    target = decision["target_multiple"]
    if not target:
        return True
    side = "long" if target > 0 else "short"
    return checks[side]["eligible"] and abs(target) <= abs(dec(entries[side]["max_target_multiple"]))


def entry_summary(rows):
    counts = {side: Counter() for side in SIDES}
    assessed = holds = 0
    for row in rows:
        checks = row.get("entry_checks")
        if not checks:
            continue
        assessed += 1
        hold = (row.get("proposal") or {}).get("intent") == "hold"
        holds += hold
        for side in SIDES:
            check = checks[side]
            counts[side]["signal_met"] += check["signal_met"]
            counts[side]["eligible"] += check["eligible"]
            counts[side]["eligible_but_held"] += check["eligible"] and hold
            counts[side]["signal_blocked_by_spread"] += check["signal_met"] and "spread" in check["failed"]
            counts[side].update("failed_" + name for name in check["failed"])
    return {"slots": len(rows), "assessed": assessed, "unassessed": len(rows) - assessed,
            "holds_when_assessed": holds, **{s: dict(counts[s]) for s in SIDES}}
