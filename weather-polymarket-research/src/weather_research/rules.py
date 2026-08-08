"""Market rule extraction and validation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from .schemas import MarketRule


class InvalidMarketRule(ValueError):
    """Raised when a market cannot be mapped to an unambiguous settlement rule."""


def _token_ids(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("clobTokenIds", payload.get("clob_token_ids", []))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidMarketRule("clobTokenIds is not valid JSON") from exc
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise InvalidMarketRule("clobTokenIds must contain token ids")
    return raw


def _text_rule(payload: Mapping[str, Any]) -> dict[str, Any]:
    question = str(payload.get("question", ""))
    description = str(payload.get("description", ""))
    resolution_source = str(payload.get("resolutionSource", payload.get("resolution_source", "")))
    if not resolution_source:
        url_match = re.search(r"https?://[^\s)]+", description)
        if url_match:
            resolution_source = url_match.group(0).rstrip(".,")
    all_text = f"{description} {resolution_source}"
    station_match = re.search(r"\bK[A-Z]{3}\b", all_text)
    if not station_match:
        raise InvalidMarketRule("station_id is not explicit in the market text")

    date_match = re.search(
        r"\bon\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\b",
        question,
        flags=re.IGNORECASE,
    )
    if not date_match:
        raise InvalidMarketRule("target date is not explicit in the question")
    end_date_raw = payload.get("endDateIso", payload.get("endDate"))
    year: int | None = None
    if end_date_raw:
        try:
            year = datetime.fromisoformat(str(end_date_raw).replace("Z", "+00:00")).year
        except ValueError:
            year = None
    year_match = re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+['’]?(\d{2,4})\b", description, re.IGNORECASE)
    if year_match:
        year = int(year_match.group(1))
        if year < 100:
            year += 2000
    if year is None:
        raise InvalidMarketRule("target year is not explicit in market metadata")
    try:
        target_date = date.fromisoformat(
            f"{year:04d}-{datetime.strptime(date_match.group(1), '%B').month:02d}-{int(date_match.group(2)):02d}"
        )
    except ValueError as exc:
        raise InvalidMarketRule("target date is invalid") from exc

    lower: float | None = None
    upper: float | None = None
    inclusive_lower = True
    inclusive_upper = True
    between_match = re.search(
        r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°?F",
        question,
        flags=re.IGNORECASE,
    )
    if between_match:
        lower, upper = float(between_match.group(1)), float(between_match.group(2))
    else:
        upper_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?F\s+or\s+below", question, flags=re.IGNORECASE)
        lower_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?F\s+or\s+higher", question, flags=re.IGNORECASE)
        if upper_match:
            upper = float(upper_match.group(1))
        elif lower_match:
            lower = float(lower_match.group(1))
        else:
            raise InvalidMarketRule("temperature bucket boundary is not explicit in the question")
    return {
        "station_id": station_match.group(0),
        "target_date": target_date,
        "timezone": "America/New_York",
        "lower_bound_f": lower,
        "upper_bound_f": upper,
        "lower_inclusive": inclusive_lower,
        "upper_inclusive": inclusive_upper,
        "resolution_source": resolution_source,
    }


def parse_market_rule(payload: Mapping[str, Any]) -> MarketRule:
    """Parse only explicitly structured weather-rule metadata; never infer boundaries."""

    structured = payload.get("weather_rule") or payload.get("weatherRule") or payload.get("resolution_rule")
    if structured is None:
        structured = _text_rule(payload)
    if not isinstance(structured, Mapping):
        raise InvalidMarketRule("weather_rule is missing or not structured")

    required = ("station_id", "target_date", "timezone", "resolution_source")
    missing = [name for name in required if not structured.get(name)]
    if missing:
        raise InvalidMarketRule(f"missing required rule fields: {', '.join(missing)}")

    token_ids = _token_ids(payload)
    values = {
        "market_id": str(payload.get("id", "")),
        "event_id": payload.get("eventId", payload.get("event_id")),
        "question": str(payload.get("question", "")),
        "description": str(payload.get("description", "")),
        "station_id": str(structured["station_id"]),
        "target_date": structured["target_date"],
        "timezone": str(structured["timezone"]),
        "lower_bound_f": structured.get("lower_bound_f"),
        "upper_bound_f": structured.get("upper_bound_f"),
        "lower_inclusive": bool(structured.get("lower_inclusive", True)),
        "upper_inclusive": bool(structured.get("upper_inclusive", True)),
        "resolution_source": str(structured["resolution_source"]),
        "yes_token_id": token_ids[0],
        "no_token_id": token_ids[1] if len(token_ids) > 1 else None,
    }
    if not values["market_id"]:
        raise InvalidMarketRule("market id is missing")
    if values["lower_bound_f"] is None and values["upper_bound_f"] is None:
        raise InvalidMarketRule("at least one temperature bound is required")
    try:
        return MarketRule.model_validate(values)
    except ValidationError as exc:
        raise InvalidMarketRule(str(exc)) from exc
