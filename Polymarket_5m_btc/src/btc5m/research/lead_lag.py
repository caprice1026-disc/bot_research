from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

from btc5m.events import parse_source_timestamp


@dataclass(frozen=True, slots=True)
class LeadLagResult:
    status: str
    reason: str | None
    event_count: int
    observations_by_horizon: dict[int, int]
    mean_signed_response_by_horizon: dict[int, float | None]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "event_count": self.event_count,
            "observations_by_horizon": self.observations_by_horizon,
            "mean_signed_response_by_horizon": self.mean_signed_response_by_horizon,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Receive-time lead-lag report",
            "",
            f"- result_state: `{self.status}`",
            f"- reason: `{self.reason or 'none'}`",
            f"- external_shocks: {self.event_count}",
            "",
            "| Horizon (ms) | Observations | Mean signed response |",
            "| ---: | ---: | ---: |",
        ]
        lines.extend(
            f"| {horizon} | {self.observations_by_horizon[horizon]} | "
            f"{('n/a' if self.mean_signed_response_by_horizon[horizon] is None else f'{self.mean_signed_response_by_horizon[horizon]:.8f}')} |"
            for horizon in sorted(self.observations_by_horizon)
        )
        lines.extend(
            [
                "",
                "This report is receive-time safe for the observed rows. It is not a "
                "profitability or trading result.",
            ]
        )
        return "\n".join(lines) + "\n"


def _float_value(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _receive_us(row: Mapping[str, object]) -> int | None:
    value = row.get("local_receive_ts")
    if value in (None, ""):
        return None
    return parse_source_timestamp(value)


def _row_price(row: Mapping[str, object]) -> float | None:
    event_type = str(row.get("event_type") or "").lower()
    bid = _float_value(row.get("bid"))
    ask = _float_value(row.get("ask"))
    quote_events = {"book", "best_bid_ask", "book_ticker", "bbo", "ticker", "price_change"}
    trade_events = {"agg_trade", "trade", "last_trade_price", "market_trade"}
    if event_type in quote_events:
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0
    price = _float_value(row.get("price"))
    if event_type in trade_events:
        return price
    if price is not None:
        return price
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _raw_mapping(row: Mapping[str, object]) -> Mapping[str, object]:
    payload = row.get("raw_payload")
    if isinstance(payload, Mapping):
        return payload
    if isinstance(payload, str) and payload:
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


def _series_identity(row: Mapping[str, object]) -> tuple[str, str]:
    raw = _raw_mapping(row)
    change = raw.get("price_change")
    change_mapping = change if isinstance(change, Mapping) else {}
    market_id = (
        row.get("market_id")
        or row.get("condition_id")
        or row.get("market")
        or raw.get("market")
        or raw.get("condition_id")
    )
    symbol = (
        row.get("symbol")
        or row.get("token_id")
        or row.get("asset_id")
        or change_mapping.get("asset_id")
        or raw.get("asset_id")
    )
    return str(market_id or ""), str(symbol or "")


def _series(
    rows: Iterable[Mapping[str, object]],
    *,
    include_source: bool,
) -> dict[tuple[str, ...], list[tuple[int, float]]]:
    grouped: dict[tuple[str, ...], list[tuple[int, float, int]]] = defaultdict(list)
    for order, row in enumerate(rows):
        timestamp = _receive_us(row)
        price = _row_price(row)
        if timestamp is None or price is None or price < 0:
            continue
        market_id, symbol = _series_identity(row)
        source = str(row.get("source") or "") if include_source else ""
        key = (source, market_id, symbol)
        grouped[key].append((timestamp, price, order))
    return {
        key: [(timestamp, price) for timestamp, price, _ in sorted(values, key=lambda item: (item[0], item[2]))]
        for key, values in grouped.items()
    }


def event_study(
    external_rows: Iterable[Mapping[str, object]],
    polymarket_rows: Iterable[Mapping[str, object]],
    *,
    shock_return: float = 0.001,
    lookback_ms: int = 500,
    horizons_ms: Sequence[int] = (100, 250, 500, 1_000, 2_000, 5_000),
) -> LeadLagResult:
    """Estimate the response after large external moves using receive timestamps.

    The external shock is measured against the latest observation at least
    ``lookback_ms`` earlier.  All joins are backward/as-of: rows arriving after
    the decision time cannot be used to define the pre-event market price.
    """
    external_input = list(external_rows)
    market_input = list(polymarket_rows)
    external = _series(external_input, include_source=True)
    market = _series(market_input, include_source=False)
    horizons = tuple(sorted({int(horizon) for horizon in horizons_ms if horizon >= 0}))
    empty_counts = {horizon: 0 for horizon in horizons}
    empty_means: dict[int, float | None] = {
        horizon: None for horizon in horizons
    }
    if not external or not market:
        return LeadLagResult(
            status="insufficient_data",
            reason=(
                "empty_series"
                if not external_input and not market_input
                else "missing_receive_time"
            ),
            event_count=0,
            observations_by_horizon=empty_counts,
            mean_signed_response_by_horizon=empty_means,
        )

    event_times: list[tuple[int, float]] = []
    lookback_us = lookback_ms * 1_000
    for series in external.values():
        external_times = [timestamp for timestamp, _ in series]
        for index, (timestamp, price) in enumerate(series):
            previous_index = bisect_right(
                external_times, timestamp - lookback_us, 0, index
            ) - 1
            if previous_index < 0:
                continue
            previous_price = series[previous_index][1]
            if previous_price <= 0:
                continue
            move = price / previous_price - 1.0
            if abs(move) >= shock_return:
                event_times.append((timestamp, 1.0 if move > 0 else -1.0))
    event_times = sorted(set(event_times))

    sums = {horizon: 0.0 for horizon in horizons}
    counts = {horizon: 0 for horizon in horizons}
    for event_time, direction in event_times:
        for series in market.values():
            market_times = [timestamp for timestamp, _ in series]
            market_prices = [price for _, price in series]
            pre_index = bisect_right(market_times, event_time) - 1
            if pre_index < 0:
                continue
            pre_price = market_prices[pre_index]
            for horizon in horizons:
                deadline = event_time + horizon * 1_000
                if market_times[-1] < deadline:
                    continue
                post_index = bisect_right(market_times, deadline) - 1
                if post_index <= pre_index:
                    continue
                response = (market_prices[post_index] - pre_price) * direction
                sums[horizon] += response
                counts[horizon] += 1

    means = {
        horizon: (sums[horizon] / counts[horizon] if counts[horizon] else None)
        for horizon in horizons
    }
    if not event_times:
        return LeadLagResult(
            status="insufficient_data",
            reason="no_external_shocks",
            event_count=0,
            observations_by_horizon=counts,
            mean_signed_response_by_horizon=means,
        )
    return LeadLagResult(
        status="exploratory" if any(counts.values()) else "insufficient_data",
        reason=None if any(counts.values()) else "no_overlapping_market_response",
        event_count=len(event_times),
        observations_by_horizon=counts,
        mean_signed_response_by_horizon=means,
    )
