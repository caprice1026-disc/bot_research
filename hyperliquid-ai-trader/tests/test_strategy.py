from __future__ import annotations

import pytest

from hyperliquid_ai_trader.strategy import StrategyPatchError, apply_strategy_patch, initial_strategy


def test_valid_strategy_patch_adds_rule_and_increments_version() -> None:
    state = initial_strategy()
    patch = {
        "base_version": 1,
        "summary": "imbalance helped",
        "operations": [
            {
                "op": "add",
                "path": "/active_rules",
                "value": "book imbalance above 0.6 supports LONG",
                "evidence": {"trade_ids": [1, 2], "net_pnl": 3.2},
            }
        ],
    }

    updated = apply_strategy_patch(state, patch, review_cycle=6)

    assert updated["version"] == 2
    assert updated["parent_version"] == 1
    assert updated["last_review_cycle"] == 6
    assert updated["active_rules"] == ["book imbalance above 0.6 supports LONG"]


def test_strategy_patch_cannot_change_risk_or_constitution() -> None:
    patch = {
        "base_version": 1,
        "summary": "unsafe",
        "operations": [
            {
                "op": "replace",
                "path": "/risk/leverage",
                "value": 100,
                "evidence": {"trade_ids": [1]},
            }
        ],
    }

    with pytest.raises(StrategyPatchError, match="path"):
        apply_strategy_patch(initial_strategy(), patch, review_cycle=6)


def test_strategy_patch_rejects_stale_base_version() -> None:
    patch = {"base_version": 0, "summary": "stale", "operations": []}

    with pytest.raises(StrategyPatchError, match="base version"):
        apply_strategy_patch(initial_strategy(), patch, review_cycle=6)
