"""Point-in-Time-safe Level 1 and Level 2 backtest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .execution import executable_yes_price, net_edge
from .pit import is_available_at_trade_time
from .schemas import OutcomeStatus


@dataclass(frozen=True)
class ExecutionConfig:
    assumed_spread: float = 0.01
    assumed_slippage: float = 0.01
    trading_fee: float = 0.01
    uncertainty_buffer: float = 0.02
    minimum_edge: float = 0.08

    def __post_init__(self) -> None:
        for name in ("assumed_spread", "assumed_slippage", "trading_fee", "uncertainty_buffer", "minimum_edge"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.minimum_edge > 1:
            raise ValueError("minimum_edge must not exceed 1")


@dataclass(frozen=True)
class BacktestCandidate:
    market_id: str
    trade_time: datetime
    forecast_issue_time: datetime
    received_time: datetime
    model_probability: float
    observed_ask: float
    outcome_yes: bool
    quantity: float


@dataclass(frozen=True)
class TradeRecord:
    market_id: str
    trade_time: datetime
    model_probability: float
    observed_ask: float
    executable_price: float
    outcome_yes: bool
    quantity: float
    gross_pnl: float
    net_pnl: float
    net_edge: float


@dataclass(frozen=True)
class BacktestResult:
    status: OutcomeStatus
    trades: list[TradeRecord]
    reason: str


def run_backtest(
    candidates: list[BacktestCandidate],
    config: ExecutionConfig,
) -> BacktestResult:
    if not candidates:
        return BacktestResult(OutcomeStatus.INSUFFICIENT_DATA, [], "no backtest candidates")

    available_count = 0
    trades: list[TradeRecord] = []
    for candidate in candidates:
        if not is_available_at_trade_time(candidate, candidate.trade_time):
            continue
        available_count += 1
        edge = net_edge(
            model_probability=candidate.model_probability,
            observed_ask=candidate.observed_ask,
            fee=config.trading_fee,
            spread=config.assumed_spread,
            slippage=config.assumed_slippage,
            uncertainty_buffer=config.uncertainty_buffer,
        )
        if edge < config.minimum_edge:
            continue
        executable_price = executable_yes_price(
            candidate.observed_ask,
            config.assumed_spread,
            config.assumed_slippage,
        )
        gross_pnl = candidate.quantity * (
            (1.0 - candidate.observed_ask) if candidate.outcome_yes else -candidate.observed_ask
        )
        net_pnl = candidate.quantity * (
            (1.0 - executable_price) if candidate.outcome_yes else -executable_price
        ) - candidate.quantity * config.trading_fee
        trades.append(
            TradeRecord(
                market_id=candidate.market_id,
                trade_time=candidate.trade_time,
                model_probability=candidate.model_probability,
                observed_ask=candidate.observed_ask,
                executable_price=executable_price,
                outcome_yes=candidate.outcome_yes,
                quantity=candidate.quantity,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                net_edge=edge,
            )
        )

    if available_count == 0:
        return BacktestResult(
            OutcomeStatus.INSUFFICIENT_DATA,
            [],
            "all candidates failed the Point-in-Time availability check",
        )
    return BacktestResult(OutcomeStatus.SUCCESS, trades, "")
