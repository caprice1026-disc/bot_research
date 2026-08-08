"""Validated data objects shared by collectors and backtests."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator


class OutcomeStatus(str, Enum):
    SUCCESS = "success"
    INSUFFICIENT_DATA = "insufficient_data"
    INVALID_MARKET_RULE = "invalid_market_rule"
    COLLECTION_ERROR = "collection_error"


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


class ForecastMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: str
    forecast_issue_time: datetime
    forecast_valid_time: datetime
    received_time: datetime
    ensemble_member: int
    temperature_f: float

    _issue_is_aware = field_validator("forecast_issue_time")(_require_timezone)
    _valid_is_aware = field_validator("forecast_valid_time")(_require_timezone)
    _received_is_aware = field_validator("received_time")(_require_timezone)


class PricePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    token_id: str
    timestamp: datetime
    price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    volume: float | None = None

    _timestamp_is_aware = field_validator("timestamp")(_require_timezone)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: str
    observation_time: datetime
    received_time: datetime
    temperature_f: float
    report_type: str | None = None

    _observation_is_aware = field_validator("observation_time")(_require_timezone)
    _received_is_aware = field_validator("received_time")(_require_timezone)


class MarketRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str
    event_id: str | None = None
    question: str
    description: str
    station_id: str
    target_date: date
    timezone: str
    lower_bound_f: float | None = None
    upper_bound_f: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    resolution_source: str
    yes_token_id: str
    no_token_id: str | None = None
