"""Public, cost-safe configuration for offline research experiments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from .simulator import ExecutionConfig


class ResearchConfigError(ValueError):
    """Raised when a research configuration is unsafe or incomplete."""


_FORBIDDEN_SECRET_KEYS = {"api_key", "private_key", "wallet", "secret"}
_PUBLIC_VENUES = {"binance_usdm_public", "hyperliquid_mainnet_public"}


@dataclass(frozen=True)
class ResearchConfig:
    experiment_id: str
    market_venue: str
    symbol: str
    feature_set: str
    allow_paid_api: bool
    budget_usd: Decimal
    trader_model: str
    reviewer_model: str
    execution: ExecutionConfig
    initial_equity: Decimal
    reference_notional: Decimal

    def public_summary(self) -> dict[str, str | bool]:
        return {
            "status": "ok",
            "experiment_id": self.experiment_id,
            "market": f"{self.market_venue}:{self.symbol}",
            "allow_paid_api": self.allow_paid_api,
            "budget_usd": format(self.budget_usd, "f"),
            "initial_equity": format(self.initial_equity, "f"),
            "reference_notional": format(self.reference_notional, "f"),
        }


def _mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchConfigError(f"{name} must be an object")
    return value


def _string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchConfigError(f"{name} must be a non-empty string")
    return value


def _decimal(value: Any, *, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ResearchConfigError(f"{name} must be a decimal value") from error
    if not result.is_finite():
        raise ResearchConfigError(f"{name} must be finite")
    return result


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_SECRET_KEYS:
                raise ResearchConfigError(f"research config must not contain {key}")
            _reject_secret_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secret_keys(child)


def load_research_config(path: Path) -> ResearchConfig:
    """Read an explicitly public JSON config without loading environment secrets."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResearchConfigError(f"cannot read research config: {path}") from error
    root = _mapping(payload, name="research config")
    _reject_secret_keys(root)
    if root.get("schema_version") != 1:
        raise ResearchConfigError("schema_version must be 1")

    market = _mapping(root.get("market"), name="market")
    venue = _string(market.get("venue"), name="market.venue")
    if venue not in _PUBLIC_VENUES:
        raise ResearchConfigError("market.venue must be a public research venue")
    if market.get("interval") != "1m":
        raise ResearchConfigError("market.interval must be 1m")

    api = _mapping(root.get("api"), name="api")
    allow_paid_api = api.get("allow_paid_api")
    if not isinstance(allow_paid_api, bool):
        raise ResearchConfigError("api.allow_paid_api must be boolean")
    budget_usd = _decimal(api.get("budget_usd"), name="api.budget_usd")
    if budget_usd < 0 or (not allow_paid_api and budget_usd != 0):
        raise ResearchConfigError("api.budget_usd must be 0 while paid API is disabled")
    if allow_paid_api and budget_usd <= 0:
        raise ResearchConfigError("api.budget_usd must be positive when paid API is enabled")

    execution_values = _mapping(root.get("execution"), name="execution")
    try:
        execution = ExecutionConfig(
            model_delay_ms=int(execution_values["model_delay_ms"]),
            max_arrival_delay_ms=int(execution_values["max_arrival_delay_ms"]),
            max_hold_ms=int(execution_values["max_hold_ms"]),
            fee_rate=_decimal(execution_values["fee_rate"], name="execution.fee_rate"),
            spread_bps=_decimal(execution_values["spread_bps"], name="execution.spread_bps"),
            slippage_bps=_decimal(
                execution_values["slippage_bps"],
                name="execution.slippage_bps",
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ResearchConfigError("execution has invalid values") from error

    if root.get("feature_set") != "common_candles_v1":
        raise ResearchConfigError("feature_set must be common_candles_v1")
    simulation = _mapping(root.get("simulation"), name="simulation")
    initial_equity = _decimal(simulation.get("initial_equity"), name="simulation.initial_equity")
    reference_notional = _decimal(
        simulation.get("reference_notional"),
        name="simulation.reference_notional",
    )
    if initial_equity <= 0 or reference_notional <= 0:
        raise ResearchConfigError("simulation equity and reference notional must be positive")
    return ResearchConfig(
        experiment_id=_string(root.get("experiment_id"), name="experiment_id"),
        market_venue=venue,
        symbol=_string(market.get("symbol"), name="market.symbol"),
        feature_set="common_candles_v1",
        allow_paid_api=allow_paid_api,
        budget_usd=budget_usd,
        trader_model=_string(api.get("trader_model"), name="api.trader_model"),
        reviewer_model=_string(api.get("reviewer_model"), name="api.reviewer_model"),
        execution=execution,
        initial_equity=initial_equity,
        reference_notional=reference_notional,
    )


def require_paid_api_permission(config: ResearchConfig) -> None:
    """Block future Batch/normal API calls until the config explicitly permits them."""

    if not config.allow_paid_api or config.budget_usd <= 0:
        raise ResearchConfigError("paid API requires allow_paid_api=true and a positive budget_usd")
