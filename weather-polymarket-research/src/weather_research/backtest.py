"""Point-in-Time-safe Level 1 and Level 2 backtest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, TypeVar

from .execution import executable_yes_price, net_edge
from .pit import is_available_at_trade_time
from .schemas import OutcomeStatus


ModelT = TypeVar("ModelT")


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
    observed_mid: float | None = None
    observed_spread: float | None = None
    price_source: str = "unknown"
    ensemble_probability: float | None = None
    gaussian_probability: float | None = None
    forecast_member_count: int = 0


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
    ensemble_probability: float | None = None
    gaussian_probability: float | None = None
    forecast_member_count: int = 0
    observed_mid: float | None = None
    price_source: str = "unknown"


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
            spread=config.assumed_spread if candidate.observed_spread is None else candidate.observed_spread,
            slippage=config.assumed_slippage,
            uncertainty_buffer=config.uncertainty_buffer,
        )
        if edge < config.minimum_edge:
            continue
        executable_price = executable_yes_price(
            candidate.observed_ask,
            config.assumed_spread if candidate.observed_spread is None else candidate.observed_spread,
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
                ensemble_probability=candidate.ensemble_probability,
                gaussian_probability=candidate.gaussian_probability,
                forecast_member_count=candidate.forecast_member_count,
                observed_mid=candidate.observed_mid,
                price_source=candidate.price_source,
            )
        )

    if available_count == 0:
        return BacktestResult(
            OutcomeStatus.INSUFFICIENT_DATA,
            [],
            "all candidates failed the Point-in-Time availability check",
        )
    return BacktestResult(OutcomeStatus.SUCCESS, trades, "")


def _read_jsonl_models(path: Path, model: Any) -> list[Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(model.model_validate(json.loads(line)))
    return rows


def run_backtest_from_artifacts(
    data_dir: Path,
    config: ExecutionConfig,
    *,
    level: int = 1,
) -> tuple[BacktestResult, Any]:
    """Run a strict-period backtest from the normalized JSONL artifacts.

    Level 1 permits the historical CLOB price as a documented proxy. Level 2
    requires a best ask in the normalized price point and therefore refuses
    to substitute a historical last price for an order-book quote.
    """

    if level not in (1, 2):
        raise ValueError("level must be 1 or 2")
    from .candidate_builder import CandidateBuildResult, build_pit_candidates
    from .schemas import ForecastMember, MarketRule, Observation, PricePoint

    try:
        normalized_dir = Path(data_dir) / "normalized"
        rules = _read_jsonl_models(normalized_dir / "market_rules.jsonl", MarketRule)
        forecasts = _read_jsonl_models(normalized_dir / "forecast_members.jsonl", ForecastMember)
        observations = _read_jsonl_models(normalized_dir / "observations.jsonl", Observation)
        prices = _read_jsonl_models(normalized_dir / "price_points.jsonl", PricePoint)
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        build = CandidateBuildResult(
            status=OutcomeStatus.INSUFFICIENT_DATA,
            candidates=[],
            reasons=[f"normalized artifact unavailable: {exc}"],
            target_dates=[],
            forecast_target_dates=[],
            observation_target_dates=[],
            priced_markets=[],
        )
        return BacktestResult(OutcomeStatus.INSUFFICIENT_DATA, [], build.reasons[0]), build

    build = build_pit_candidates(
        rules,
        forecasts,
        observations,
        prices,
        require_best_ask=level == 2,
    )
    if build.status is not OutcomeStatus.SUCCESS:
        reason = "; ".join(build.reasons[:12]) or "forecast, observation, and price periods are not aligned"
        if len(build.reasons) > 12:
            reason += f"; and {len(build.reasons) - 12} more coverage gaps"
        return BacktestResult(OutcomeStatus.INSUFFICIENT_DATA, [], reason), build
    return run_backtest(build.candidates, config), build
