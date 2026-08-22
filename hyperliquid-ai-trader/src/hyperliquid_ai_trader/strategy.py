"""Versioned, allow-listed strategy memory patches."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class StrategyPatchError(ValueError):
    """Raised when a reviewer patch is stale or crosses its authority boundary."""


def initial_strategy() -> dict[str, Any]:
    return {
        "version": 1,
        "parent_version": None,
        "market_hypothesis": "短期BTC価格はモメンタム、板の偏り、ボラティリティの一致時に方向性を持つという初期仮説。",
        "active_rules": [],
        "failure_modes": [],
        "confidence_calibration": {"long": 0.0, "short": 0.0},
        "last_review_cycle": 0,
    }


def _require_evidence(operation: dict[str, Any]) -> None:
    evidence = operation.get("evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("trade_ids"), list):
        raise StrategyPatchError("operation evidence is required")


def apply_strategy_patch(
    state: dict[str, Any],
    patch: dict[str, Any],
    *,
    review_cycle: int,
) -> dict[str, Any]:
    current_version = int(state.get("version", 0))
    if patch.get("base_version") != current_version:
        raise StrategyPatchError("base version does not match current strategy")
    operations = patch.get("operations")
    if not isinstance(operations, list):
        raise StrategyPatchError("operations must be a list")

    updated = deepcopy(state)
    for operation in operations:
        if not isinstance(operation, dict):
            raise StrategyPatchError("operation must be an object")
        _require_evidence(operation)
        op = operation.get("op")
        path = operation.get("path")
        value = operation.get("value")

        if path in {"/active_rules", "/failure_modes"}:
            target = updated[path[1:]]
            if not isinstance(value, str) or not value.strip():
                raise StrategyPatchError("rule value must be non-empty text")
            normalized = value.strip()
            if op == "add":
                if normalized not in target:
                    target.append(normalized)
            elif op == "remove":
                if normalized in target:
                    target.remove(normalized)
            else:
                raise StrategyPatchError("list path supports add or remove")
        elif path == "/market_hypothesis":
            if op != "replace" or not isinstance(value, str) or not value.strip():
                raise StrategyPatchError("market hypothesis requires replacement text")
            updated["market_hypothesis"] = value.strip()
        elif path in {
            "/confidence_calibration/long",
            "/confidence_calibration/short",
        }:
            if op != "replace" or not isinstance(value, (int, float)) or isinstance(value, bool):
                raise StrategyPatchError("confidence calibration requires a number")
            if not -0.5 <= float(value) <= 0.5:
                raise StrategyPatchError("confidence calibration is outside the allowed range")
            updated["confidence_calibration"][path.rsplit("/", 1)[1]] = float(value)
        else:
            raise StrategyPatchError("patch path is not allowed")

    updated["parent_version"] = current_version
    updated["version"] = current_version + 1
    updated["last_review_cycle"] = review_cycle
    return updated
