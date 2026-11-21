"""Codex-authored starting rules for the local Testnet improvement run."""
from .entries import SIDES, multiples, validate_entries


STRATEGY_RUN = "hourly-pullback"


def initial_strategy(cfg):
    entries = {}
    for side in SIDES:
        sizes = multiples(cfg, side)
        alignment = "above" if side == "long" else "below"
        against = "below" if side == "long" else "above"
        extreme = "low" if side == "long" else "high"
        entries[side] = {
            "enabled": bool(sizes),
            "trend": "hourly_close_vs_sma20",
            "trigger": "sma20_retest",
            "max_spread_bps": 12,
            "max_target_multiple": sizes[0] if sizes else "0.0",
            "rationale": f"Join the hourly trend when a closed 5m candle tests its SMA20 and holds {alignment} it.",
            "invalidation": f"Use a protective stop beyond the retest {extreme}, and exit if the 1h close moves {against} its SMA20.",
            "reconsider_when": "Compare with a fresh SMA20 cross if the retest gate misses executable trend continuation.",
        }
    validate_entries(entries, cfg)
    return {
        "market_hypothesis": "A retest of the short-term mean in the direction of the hourly trend offers a controlled entry on either side.",
        "active_rules": [
            "Only consider an entry on a completed 5m retest aligned with the latest completed hourly close.",
            "Use a protective stop beyond the retest extreme. Pass if the stop distance or displayed liquidity makes the trade unattractive.",
            "Manage exits before considering additions. Respect the stored hourly exit and protect any remaining quantity.",
        ],
        "failure_modes": [
            "Choppy markets can flip the hourly trend and produce repeated small losses.",
            "A ten-minute decision interval samples every other completed 5m candle and can miss brief signals.",
            "A narrow displayed spread can still have too little depth for a low-cost fill.",
        ],
        "change_reason": "Independent symmetric baseline commissioned for the self-improvement run.",
        "evidence_ids": [],
        "entries": entries,
        "improvement": {
            "previous": {"status": "baseline", "finding": "New initial strategy.",
                         "limit": "No results from this run are available yet."},
            "next": {"action": "observe", "target": {"side": "none", "field": "none"},
                     "hypothesis": "Hourly alignment with a 5m retest produces feasible entries in both directions when liquidity permits.",
                     "metric": "Executable entry opportunities and net observed trading result after fees and funding.",
                     "expected_direction": "stable",
                     "accept_if": "Keep collecting forward observations while triggers, depth and fills are consistent with the hypothesis.",
                     "rollback_if": "Test a cited alternative if repeated executable opportunities are missed or the net trading result deteriorates across completed positions."},
        },
    }
