"""Join normalized weather and market data into Point-in-Time candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import floor
from statistics import fmean, pstdev
from typing import Sequence
from zoneinfo import ZoneInfo

from .backtest import BacktestCandidate
from .pit import is_available_at_trade_time
from .probability import Bucket, ensemble_bucket_probability, gaussian_bucket_probability
from .schemas import ForecastMember, MarketRule, Observation, OutcomeStatus, PricePoint


@dataclass(frozen=True)
class CandidateBuildResult:
    status: OutcomeStatus
    candidates: list[BacktestCandidate]
    reasons: list[str]
    target_dates: list[str]
    forecast_target_dates: list[str]
    observation_target_dates: list[str]
    priced_markets: list[str]


def _target_date_for_forecast(forecast: ForecastMember, timezone_name: str) -> date:
    local_end_date = forecast.forecast_valid_time.astimezone(ZoneInfo(timezone_name)).date()
    return local_end_date - timedelta(days=1)


def _daily_maxima(
    observations: Sequence[Observation],
    timezone_name: str,
) -> dict[tuple[str, date], float]:
    grouped: dict[tuple[str, date], list[float]] = {}
    zone = ZoneInfo(timezone_name)
    for observation in observations:
        key = (observation.station_id, observation.observation_time.astimezone(zone).date())
        grouped.setdefault(key, []).append(observation.temperature_f)
    return {key: max(values) for key, values in grouped.items()}


def _resolved_whole_degree(value: float) -> float:
    """Match the market's stated whole-degree Fahrenheit resolution."""

    return float(floor(value + 0.5) if value >= 0 else -floor(abs(value) + 0.5))


def _bucket_for_rule(rule: MarketRule) -> Bucket:
    return Bucket(
        lower=rule.lower_bound_f,
        upper=rule.upper_bound_f,
        lower_inclusive=rule.lower_inclusive,
        upper_inclusive=rule.upper_inclusive,
    )


def _latest_forecast_members(
    forecasts: Sequence[ForecastMember],
    trade_time: datetime,
    rule: MarketRule,
) -> list[ForecastMember]:
    eligible = [
        forecast
        for forecast in forecasts
        if forecast.station_id == rule.station_id
        and _target_date_for_forecast(forecast, rule.timezone) == rule.target_date
        and is_available_at_trade_time(forecast, trade_time)
    ]
    latest: dict[int, ForecastMember] = {}
    for forecast in eligible:
        current = latest.get(forecast.ensemble_member)
        if current is None or (forecast.forecast_issue_time, forecast.received_time) > (
            current.forecast_issue_time,
            current.received_time,
        ):
            latest[forecast.ensemble_member] = forecast
    return [latest[member] for member in sorted(latest)]


def _quote_from_price_point(
    point: PricePoint,
    require_best_ask: bool,
) -> tuple[float, float, float | None, str] | None:
    if point.best_ask is not None:
        if not 0 <= point.best_ask <= 1:
            return None
        mid = point.price
        if point.best_bid is not None:
            if not 0 <= point.best_bid <= 1 or point.best_bid > point.best_ask:
                return None
            mid = (point.best_bid + point.best_ask) / 2
        elif mid is None:
            mid = point.best_ask
        return point.best_ask, mid, 0.0, "clob_best_ask"
    if require_best_ask or point.price is None or not 0 <= point.price <= 1:
        return None
    # The historical endpoint returns a price, not a historical order-book ask.
    # Level 1 therefore uses it as a documented mid/last-price proxy.
    return point.price, point.price, None, "historical_price_proxy"


def _gaussian_probability(values: Sequence[float], bucket: Bucket) -> float:
    mean = fmean(values)
    standard_deviation = pstdev(values)
    if standard_deviation == 0:
        return ensemble_bucket_probability([mean], bucket)
    return gaussian_bucket_probability(mean, standard_deviation, bucket)


def build_pit_candidates(
    rules: Sequence[MarketRule],
    forecasts: Sequence[ForecastMember],
    observations: Sequence[Observation],
    prices: Sequence[PricePoint],
    *,
    require_best_ask: bool = False,
    minimum_members: int = 1,
    quantity: float = 1.0,
) -> CandidateBuildResult:
    """Build one candidate per available price point without future leakage."""

    if minimum_members <= 0:
        raise ValueError("minimum_members must be positive")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    candidates: list[BacktestCandidate] = []
    reasons: list[str] = []
    reason_keys: set[tuple[str, str]] = set()

    def add_reason(key: tuple[str, str], message: str) -> None:
        if key not in reason_keys:
            reason_keys.add(key)
            reasons.append(message)

    target_dates = sorted({rule.target_date.isoformat() for rule in rules})
    forecast_dates = sorted(
        {
            _target_date_for_forecast(forecast, "America/New_York").isoformat()
            for forecast in forecasts
        }
    )
    observation_dates = sorted(
        {
            observation.observation_time.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            for observation in observations
        }
    )
    priced_markets = sorted({point.market_id for point in prices})

    for rule in rules:
        maxima = _daily_maxima(observations, rule.timezone)
        outcome_value = maxima.get((rule.station_id, rule.target_date))
        if outcome_value is None:
            add_reason(
                ("observation", rule.target_date.isoformat()),
                f"observation missing for {rule.target_date.isoformat()}",
            )
            continue
        outcome_yes = ensemble_bucket_probability(
            [_resolved_whole_degree(outcome_value)], _bucket_for_rule(rule)
        ) == 1.0
        rule_forecasts = [forecast for forecast in forecasts if forecast.station_id == rule.station_id]
        if not any(_target_date_for_forecast(forecast, rule.timezone) == rule.target_date for forecast in rule_forecasts):
            add_reason(
                ("forecast", rule.target_date.isoformat()),
                f"GEFS forecast missing for {rule.target_date.isoformat()}",
            )
            continue
        rule_prices = sorted(
            [
                point
                for point in prices
                if point.market_id == rule.market_id and point.token_id == rule.yes_token_id
            ],
            key=lambda point: point.timestamp,
        )
        if not rule_prices:
            add_reason(("price", rule.market_id), f"{rule.market_id}: CLOB price history missing")
            continue
        made_candidate = False
        for point in rule_prices:
            available = _latest_forecast_members(rule_forecasts, point.timestamp, rule)
            if len(available) < minimum_members:
                continue
            quote = _quote_from_price_point(point, require_best_ask)
            if quote is None:
                continue
            observed_ask, observed_mid, observed_spread, price_source = quote
            values = [forecast.temperature_f for forecast in available]
            bucket = _bucket_for_rule(rule)
            ensemble_probability = ensemble_bucket_probability(values, bucket)
            gaussian_probability = _gaussian_probability(values, bucket)
            candidates.append(
                BacktestCandidate(
                    market_id=rule.market_id,
                    trade_time=point.timestamp,
                    forecast_issue_time=max(forecast.forecast_issue_time for forecast in available),
                    received_time=max(forecast.received_time for forecast in available),
                    model_probability=ensemble_probability,
                    observed_ask=observed_ask,
                    outcome_yes=outcome_yes,
                    quantity=quantity,
                    observed_mid=observed_mid,
                    observed_spread=observed_spread,
                    price_source=price_source,
                    ensemble_probability=ensemble_probability,
                    gaussian_probability=gaussian_probability,
                    forecast_member_count=len(available),
                )
            )
            made_candidate = True
        if not made_candidate:
            if require_best_ask:
                add_reason(
                    ("ask", rule.market_id),
                    f"{rule.market_id}: no Point-in-Time best ask was available",
                )
            else:
                add_reason(
                    ("pit", rule.market_id),
                    f"{rule.market_id}: no price point had enough Point-in-Time forecasts",
                )

    status = OutcomeStatus.SUCCESS if candidates and not reasons else OutcomeStatus.INSUFFICIENT_DATA
    return CandidateBuildResult(
        status=status,
        candidates=candidates,
        reasons=reasons,
        target_dates=target_dates,
        forecast_target_dates=forecast_dates,
        observation_target_dates=observation_dates,
        priced_markets=priced_markets,
    )
