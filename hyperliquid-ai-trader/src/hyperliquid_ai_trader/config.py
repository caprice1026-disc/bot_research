"""Environment-backed configuration with a hard Testnet boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping


class ConfigError(ValueError):
    """Raised when configuration cannot be used safely."""


def _required(values: Mapping[str, str | None], name: str) -> str:
    value = (values.get(name) or "").strip()
    if not value:
        raise ConfigError(f"{name} is missing")
    return value


def _decimal(values: Mapping[str, str | None], name: str, default: str) -> Decimal:
    raw = (values.get(name) or default).strip()
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError(f"{name} must be numeric") from exc


def _integer(values: Mapping[str, str | None], name: str, default: int) -> int:
    raw = (values.get(name) or str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _boolean(values: Mapping[str, str | None], name: str, default: bool) -> bool:
    raw = (values.get(name) or str(default)).strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    wallet_address: str
    private_key: str = field(repr=False)
    gemini_api_key: str = field(repr=False)
    network: str = "testnet"
    expected_account_mode: str | None = None
    coin: str = "BTC"
    margin_mode: str = "isolated"
    leverage: int = 5
    risk_per_trade_pct: Decimal = Decimal("1.0")
    max_position_notional_usd: Decimal = Decimal("250")
    max_daily_loss_pct: Decimal = Decimal("20")
    max_drawdown_pct: Decimal = Decimal("25")
    min_stop_loss_pct: Decimal = Decimal("0.10")
    max_stop_loss_pct: Decimal = Decimal("1.00")
    min_take_profit_pct: Decimal = Decimal("0.10")
    max_take_profit_pct: Decimal = Decimal("2.00")
    max_hold_seconds: int = 300
    trader_interval_seconds: int = 300
    review_interval_seconds: int = 1800
    mandatory_entry: bool = True
    trader_model: str = "gemini-3.6-flash"
    reviewer_model: str = "gemini-3.6-flash"
    trader_temperature: float = 0.7
    reviewer_temperature: float = 0.4
    execution_mode: str = "dry_run"
    max_entry_slippage_bps: Decimal = Decimal("50")
    database_path: str = "data/trader.db"
    strategy_path: str = "state/strategy.json"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> "Settings":
        network = (values.get("HL_NETWORK") or "testnet").strip().lower()
        if network != "testnet":
            raise ConfigError("HL_NETWORK is testnet only in v0.1")

        margin_mode = (values.get("MARGIN_MODE") or "isolated").strip().lower()
        if margin_mode not in {"isolated", "cross"}:
            raise ConfigError("MARGIN_MODE must be isolated or cross")

        execution_mode = (values.get("EXECUTION_MODE") or "dry_run").strip().lower()
        if execution_mode not in {"dry_run", "testnet_live"}:
            raise ConfigError("EXECUTION_MODE must be dry_run or testnet_live")

        min_sl = _decimal(values, "MIN_STOP_LOSS_PCT", "0.10")
        max_sl = _decimal(values, "MAX_STOP_LOSS_PCT", "1.00")
        min_tp = _decimal(values, "MIN_TAKE_PROFIT_PCT", "0.10")
        max_tp = _decimal(values, "MAX_TAKE_PROFIT_PCT", "2.00")
        if min_sl <= 0 or max_sl < min_sl:
            raise ConfigError("stop loss range is invalid")
        if min_tp <= 0 or max_tp < min_tp:
            raise ConfigError("take profit range is invalid")

        leverage = _integer(values, "LEVERAGE", 5)
        if leverage < 1:
            raise ConfigError("LEVERAGE must be at least 1")

        shared_model = (values.get("GEMINI_MODEL") or "gemini-3.6-flash").strip()
        expected_account_mode = (values.get("HL_EXPECTED_ACCOUNT_MODE") or "").strip() or None
        valid_account_modes = {"unifiedAccount", "portfolioMargin", "disabled", "default", "dexAbstraction"}
        if expected_account_mode is not None and expected_account_mode not in valid_account_modes:
            raise ConfigError("HL_EXPECTED_ACCOUNT_MODE must be a supported account mode")

        return cls(
            wallet_address=_required(values, "HL_test_wallet"),
            private_key=_required(values, "HL_test_wallet_private_key"),
            gemini_api_key=_required(values, "GEMINI_API_KEY"),
            network=network,
            expected_account_mode=expected_account_mode,
            coin=(values.get("TRADING_COIN") or "BTC").strip().upper(),
            margin_mode=margin_mode,
            leverage=leverage,
            risk_per_trade_pct=_decimal(values, "RISK_PER_TRADE_PCT", "1.0"),
            max_position_notional_usd=_decimal(values, "MAX_POSITION_NOTIONAL_USD", "250"),
            max_daily_loss_pct=_decimal(values, "MAX_DAILY_LOSS_PCT", "20"),
            max_drawdown_pct=_decimal(values, "MAX_DRAWDOWN_PCT", "25"),
            min_stop_loss_pct=min_sl,
            max_stop_loss_pct=max_sl,
            min_take_profit_pct=min_tp,
            max_take_profit_pct=max_tp,
            max_hold_seconds=_integer(values, "MAX_HOLD_SECONDS", 300),
            trader_interval_seconds=_integer(values, "TRADER_INTERVAL_SECONDS", 300),
            review_interval_seconds=_integer(values, "REVIEW_INTERVAL_SECONDS", 1800),
            mandatory_entry=_boolean(values, "MANDATORY_ENTRY", True),
            trader_model=(values.get("TRADER_MODEL") or shared_model).strip(),
            reviewer_model=(values.get("REVIEWER_MODEL") or shared_model).strip(),
            trader_temperature=float(values.get("TRADER_TEMPERATURE") or "0.7"),
            reviewer_temperature=float(values.get("REVIEWER_TEMPERATURE") or "0.4"),
            execution_mode=execution_mode,
            max_entry_slippage_bps=_decimal(values, "MAX_ENTRY_SLIPPAGE_BPS", "50"),
            database_path=(values.get("DATABASE_PATH") or "data/trader.db").strip(),
            strategy_path=(values.get("STRATEGY_PATH") or "state/strategy.json").strip(),
        )
