"""Environment configuration; credentials never enter the evaluation fingerprint."""
from dataclasses import asdict, dataclass, fields
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os


def dec(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a decimal")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid decimal") from None
    if not result.is_finite():
        raise ValueError("non-finite decimal")
    return result


@dataclass(frozen=True)
class Config:
    bot_id: str = "btc-shadow-v1"
    trader_model: str = "gemini-3.5-flash"
    reviewer_model: str = "gemini-3.8-flash"
    initial_equity: Decimal = Decimal("500")
    max_exposure: Decimal = Decimal("3")
    stop_risk: Decimal = Decimal("0.03")
    drawdown_limit: Decimal = Decimal("0.10")
    evaluation_dd: Decimal = Decimal("0.15")
    rebalance_threshold: Decimal = Decimal("0.05")
    price_drift_limit: Decimal = Decimal("0.003")
    fee_rate: Decimal = Decimal("0.00045")
    slippage_bps: Decimal = Decimal("2")
    min_notional: Decimal = Decimal("10")
    trader_timeout_seconds: int = 60
    reviewer_timeout_seconds: int = 180
    cycle_timeout_seconds: int = 270
    max_start_delay_seconds: int = 60
    strategy_ttl_seconds: int = 21600
    review_interval_seconds: int = 7200
    cooldown_seconds: int = 86400
    llm_monthly_budget_usd: Decimal = Decimal("20")
    llm_budget_action: str = "notify_only"
    billing_tier: str = "paid"
    trader_input_usd_per_million: Decimal = Decimal("1.50")
    trader_output_usd_per_million: Decimal = Decimal("9")
    reviewer_input_usd_per_million: Decimal = Decimal("1.50")
    reviewer_output_usd_per_million: Decimal = Decimal("7.50")
    trader_max_output_tokens: int = 2048
    reviewer_max_output_tokens: int = 4096
    trader_thinking_level: str = "low"
    reviewer_thinking_level: str = "low"
    max_prompt_bytes: int = 100000
    news_url: str = "https://www.panewslab.com/rss.xml?lang=en&type=NORMAL%2CNEWS"
    news_max_items: int = 30
    news_max_age_seconds: int = 86400
    book_max_age_seconds: int = 15

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, Decimal) and (not value.is_finite() or value < 0):
                raise ValueError(f"invalid {field.name}")
            if type(value) is int and value <= 0:
                raise ValueError(f"invalid {field.name}")
        if not self.bot_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in self.bot_id):
            raise ValueError("bot_id must be a path-safe identifier")
        if not Decimal(".05") <= self.drawdown_limit <= Decimal(".15"):
            raise ValueError("drawdown_limit must be 5 to 15 percent")
        if not 0 < self.stop_risk < 1 or not 0 < self.max_exposure <= 3 or self.initial_equity <= 0:
            raise ValueError("invalid risk configuration")
        if not 0 < self.evaluation_dd < 1 or not 0 < self.price_drift_limit < 1 or not 0 <= self.rebalance_threshold < 1:
            raise ValueError("invalid evaluation thresholds")
        if self.fee_rate >= 1 or self.slippage_bps >= 1000 or self.min_notional <= 0:
            raise ValueError("invalid costs")
        if self.llm_budget_action not in {"notify_only", "stop"} or self.billing_tier not in {"free", "paid"}:
            raise ValueError("invalid budget mode")
        if self.cycle_timeout_seconds > 270 or max(self.trader_timeout_seconds, self.reviewer_timeout_seconds) + 20 > self.cycle_timeout_seconds:
            raise ValueError("timeouts leave no persistence margin")
        if not self.news_url.startswith("https://"):
            raise ValueError("RSS requires HTTPS")

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        defaults = cls()
        values = {}
        for field in fields(cls):
            raw = env.get(field.name.upper())
            if raw is not None:
                default = getattr(defaults, field.name)
                values[field.name] = dec(raw) if isinstance(default, Decimal) else type(default)(raw)
        return cls(**values)

    def public(self):
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.public(), sort_keys=True).encode()).hexdigest()
